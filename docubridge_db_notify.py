# -*- coding: utf-8 -*-
"""Lead-notification outbox (notify_log) and the once-a-day digest marker (digest_log).

notify_log: one row per (ticket, channel). Claims are a single atomic
UPDATE … RETURNING (no automatic retry: an unknown outcome is picked up later by the
lease expiry rather than sent twice right away). Shipments that existed before the
table was created are marked 'legacy' in the same migration transaction, so old
orders are never (re)sent.
"""
from typing import Dict, List, Optional

import psycopg2.extras

from docubridge_config import DB_URL
from docubridge_db_pool_a import get_conn, return_conn, db_run, begin_transaction

LEASE_MIN = 10            # a 'sending' row older than this is considered abandoned
SWEEP_GRACE_MIN = 1       # shipments younger than this are left to the request path
SWEEP_WINDOW_H = 24
CHANNELS = ("telegram", "email")
_MIGRATION_LOCK = 4242013


def _run(label, fn, default=None, retry=True):
    return db_run(label, fn, default, retry, getconn=lambda: get_conn(), putconn=lambda c: return_conn(c))


NOTIFY_DDL = """
CREATE TABLE IF NOT EXISTS notify_log (
    ticket      TEXT NOT NULL,
    channel     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INT  NOT NULL DEFAULT 0,
    next_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at  TIMESTAMPTZ,
    target      TEXT,
    message_id  TEXT,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ,
    PRIMARY KEY (ticket, channel)
);
CREATE INDEX IF NOT EXISTS notify_log_due_idx ON notify_log (status, next_at);
CREATE TABLE IF NOT EXISTS digest_log (
    day         DATE PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'sending',
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at     TIMESTAMPTZ,
    info        TEXT
);
CREATE TABLE IF NOT EXISTS handoff_log (
    id          BIGSERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'client',
    reason      TEXT,
    ticket      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS handoff_log_chat_idx ON handoff_log (chat_id, scope, created_at);
"""


def ensure_notify_tables() -> bool:
    """Idempotent migration. First run only: every existing shipment → 'legacy'."""
    if not DB_URL:
        return False

    def _do(conn):
        begin_transaction(conn)
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        cur.execute("SELECT to_regclass('notify_log') IS NOT NULL")
        existed = bool(cur.fetchone()[0])
        cur.execute(NOTIFY_DDL)
        legacy = 0
        if not existed:
            cur.execute(
                """
                INSERT INTO notify_log (ticket, channel, status, last_error)
                SELECT s.ticket, c.channel, 'legacy', 'created before notify outbox'
                  FROM shipments s CROSS JOIN (VALUES ('telegram'), ('email')) AS c(channel)
                ON CONFLICT (ticket, channel) DO NOTHING
                """
            )
            legacy = cur.rowcount or 0
        conn.commit()
        cur.close()
        return {"existed": existed, "legacy": legacy}

    res = _run("ensure_notify_tables", _do, default=None)
    if res is None:
        print("[NOTIFY] tables FAILED (will retry later)")
        return False
    if not res["existed"]:
        print(f"[NOTIFY] notify_log created; {res['legacy'] // 2} existing orders marked legacy (not sent)")
    return True


def enqueue_notify(ticket: str, rows: List[Dict]) -> int:
    """rows: [{channel, status, target, last_error}] — ON CONFLICT DO NOTHING (never resets)."""
    if not DB_URL or not rows:
        return 0

    def _do(conn):
        cur = conn.cursor()
        n = 0
        for r in rows:
            cur.execute(
                """
                INSERT INTO notify_log (ticket, channel, status, target, last_error)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ticket, channel) DO NOTHING
                """,
                (ticket, r["channel"], r.get("status", "pending"),
                 None if r.get("target") is None else str(r["target"]), r.get("last_error")),
            )
            n += cur.rowcount or 0
        cur.close()
        return n

    return int(_run("enqueue_notify", _do, default=0) or 0)


def claim_notify(ticket: str, channel: str) -> Optional[int]:
    """Lease one (ticket, channel) for sending. Returns the attempt number or None."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE notify_log
               SET status = 'sending', claimed_at = NOW(), attempts = attempts + 1
             WHERE ticket = %s AND channel = %s
               AND ((status IN ('pending', 'retry') AND next_at <= NOW())
                    OR (status = 'sending' AND claimed_at < NOW() - (%s * INTERVAL '1 minute')))
            RETURNING attempts
            """,
            (ticket, channel, LEASE_MIN),
        )
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row else None

    return _run("claim_notify", _do, default=None, retry=False)


def finish_notify(ticket: str, channel: str, status: str, message_id=None,
                  error: Optional[str] = None, retry_in_s: Optional[float] = None,
                  target=None) -> bool:
    """status: sent | retry | failed | skipped."""
    if not DB_URL:
        return False

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE notify_log
               SET status = %s,
                   message_id = COALESCE(%s, message_id),
                   last_error = %s,
                   target = COALESCE(%s, target),
                   sent_at = CASE WHEN %s = 'sent' THEN NOW() ELSE sent_at END,
                   next_at = NOW() + (%s * INTERVAL '1 second'),
                   claimed_at = NULL
             WHERE ticket = %s AND channel = %s
            """,
            (status, None if message_id is None else str(message_id),
             (error or None) and str(error)[:300], None if target is None else str(target),
             status, float(retry_in_s or 0), ticket, channel),
        )
        cur.close()
        return True

    return bool(_run("finish_notify", _do, default=False))


def get_notify_status(ticket: str, channel: str = "telegram") -> Optional[str]:
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute("SELECT status FROM notify_log WHERE ticket = %s AND channel = %s", (ticket, channel))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    return _run("get_notify_status", _do, default=None)


def list_due_notify(limit: int = 20) -> List[Dict]:
    if not DB_URL:
        return []

    def _do(conn):
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT n.ticket, n.channel, n.attempts, s.chat_id, s.payload, s.created_at
              FROM notify_log n JOIN shipments s ON s.ticket = n.ticket
             WHERE (n.status IN ('pending', 'retry') AND n.next_at <= NOW())
                OR (n.status = 'sending' AND n.claimed_at < NOW() - (%s * INTERVAL '1 minute'))
             ORDER BY n.created_at ASC
             LIMIT %s
            """,
            (LEASE_MIN, int(limit)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    return _run("list_due_notify", _do, default=[])


def list_unqueued_shipments(limit: int = 20) -> List[Dict]:
    """Recent shipments with no notify_log row at all (request-path enqueue lost)."""
    if not DB_URL:
        return []

    def _do(conn):
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT s.ticket, s.chat_id, s.payload, s.created_at
              FROM shipments s
             WHERE s.created_at > NOW() - (%s * INTERVAL '1 hour')
               AND s.created_at < NOW() - (%s * INTERVAL '1 minute')
               AND NOT EXISTS (SELECT 1 FROM notify_log n WHERE n.ticket = s.ticket)
             ORDER BY s.created_at ASC
             LIMIT %s
            """,
            (SWEEP_WINDOW_H, SWEEP_GRACE_MIN, int(limit)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    return _run("list_unqueued_shipments", _do, default=[])


# ------------ daily digest ------------

def claim_digest(day) -> bool:
    """True once per local day (or again after a failed / abandoned attempt)."""
    if not DB_URL:
        return False

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO digest_log (day) VALUES (%s)
            ON CONFLICT (day) DO UPDATE SET status = 'sending', claimed_at = NOW()
             WHERE (digest_log.status = 'failed' AND digest_log.claimed_at < NOW() - INTERVAL '5 minutes')
                OR (digest_log.status = 'sending' AND digest_log.claimed_at < NOW() - (%s * INTERVAL '1 minute'))
            RETURNING day
            """,
            (day, LEASE_MIN),
        )
        ok = cur.fetchone() is not None
        cur.close()
        return ok

    return bool(_run("claim_digest", _do, default=False, retry=False))


def finish_digest(day, ok: bool, info: str = "") -> None:
    if not DB_URL:
        return

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE digest_log SET status = %s, info = %s,
                   sent_at = CASE WHEN %s THEN NOW() ELSE sent_at END
             WHERE day = %s
            """,
            ("sent" if ok else "failed", (info or "")[:300], bool(ok), day),
        )
        cur.close()
        return True

    _run("finish_digest", _do)


def digest_shipments(exclude_chat_id: int, limit: int = 300) -> Optional[List[Dict]]:
    """New (24 h) + still 'accepted' older shipments, admin test orders excluded.
    None = DB error (the digest is then not marked as sent)."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT ticket, chat_id, payload, status, created_at,
                   (created_at >= NOW() - INTERVAL '24 hours') AS is_new
              FROM shipments
             WHERE chat_id <> %s
               AND (created_at >= NOW() - INTERVAL '24 hours'
                    OR status IN ('accepted', 'принято'))
             ORDER BY created_at ASC
             LIMIT %s
            """,
            (int(exclude_chat_id or 0), int(limit)),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    return _run("digest_shipments", _do, default=None)


# ------------ «Нужен человек» throttle (handoff_log) ------------

def claim_handoff(chat_id: int, scope: str, reason: str, ticket: Optional[str],
                  window_min: int) -> Optional[bool]:
    """True = first handoff card for (chat, scope) within window_min → send it;
    False = one was sent recently → skip; None = DB unavailable (caller decides)."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO handoff_log (chat_id, scope, reason, ticket)
            SELECT %s, %s, %s, %s
             WHERE NOT EXISTS (
                   SELECT 1 FROM handoff_log
                    WHERE chat_id = %s AND scope = %s
                      AND created_at > NOW() - (%s * INTERVAL '1 minute'))
            RETURNING id
            """,
            (int(chat_id), scope, reason, ticket, int(chat_id), scope, int(window_min)),
        )
        ok = cur.fetchone() is not None
        conn.commit()
        cur.close()
        return ok

    return _run("claim_handoff", _do, default=None, retry=False)


def set_pay_currency(ticket: str, chat_id: int, currency: str):
    """Store the chosen currency on the client's own order (idempotent overwrite).
    dict (the order payload) = stored; False = no such order for this chat (stale/foreign
    button); None = DB down."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE shipments SET pay_currency = %s, pay_currency_at = NOW()
             WHERE UPPER(ticket) = UPPER(%s) AND chat_id = %s
            RETURNING payload
            """,
            (currency, ticket, int(chat_id)),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return (dict(row[0] or {}) if isinstance(row[0], dict) else {}) if row else False

    return _run("set_pay_currency", _do, default=None, retry=False)


def accept_terms(ticket: str, chat_id: int):
    """Mark «условия подходят» once per order (terms_accepted_at stays the first time).
    {'first': bool, 'at': ts, 'payload': dict} | False (no such order in this chat) | None (DB down)."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE shipments SET terms_accepted_at = NOW()
             WHERE UPPER(ticket) = UPPER(%s) AND chat_id = %s AND terms_accepted_at IS NULL
            RETURNING terms_accepted_at, payload
            """,
            (ticket, int(chat_id)),
        )
        row = cur.fetchone()
        first = row is not None
        if not first:
            cur.execute(
                "SELECT terms_accepted_at, payload FROM shipments WHERE UPPER(ticket) = UPPER(%s) AND chat_id = %s",
                (ticket, int(chat_id)),
            )
            row = cur.fetchone()
        conn.commit()
        cur.close()
        if not row:
            return False
        return {"first": first, "at": row[0], "payload": dict(row[1] or {}) if isinstance(row[1], dict) else {}}

    return _run("accept_terms", _do, default=None, retry=False)


def mark_pay_requested(ticket: str):
    """Documents arrived, payment requested: keep the first pay_requested_at. True | False (no order) | None (DB down)."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE shipments SET pay_requested_at = COALESCE(pay_requested_at, NOW())
             WHERE UPPER(ticket) = UPPER(%s)
            RETURNING pay_requested_at
            """,
            (ticket,),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return bool(row)

    return _run("mark_pay_requested", _do, default=None, retry=False)


def pay_requested(ticket: str, chat_id: int):
    """True once the admin requested payment for this chat's order; False otherwise; None = DB down."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT pay_requested_at FROM shipments WHERE UPPER(ticket) = UPPER(%s) AND chat_id = %s",
            (ticket, int(chat_id)),
        )
        row = cur.fetchone()
        cur.close()
        return bool(row and row[0])

    return _run("pay_requested", _do, default=None)


def get_order_pay_info(ticket: str) -> Optional[Dict]:
    """{'ticket', 'chat_id', 'payload', 'pay_currency'} for the admin payment request; None if missing/DB down."""
    if not DB_URL:
        return None

    def _do(conn):
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT ticket, chat_id, payload, pay_currency FROM shipments WHERE UPPER(ticket) = UPPER(%s)",
            (ticket,),
        )
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None

    return _run("get_order_pay_info", _do, default=None)
