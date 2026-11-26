# -*- coding: utf-8 -*-
"""DocuBridge DB pool — fork-safe, lazy, self-healing.

* Nothing connects at import time. The pool is created lazily in the process
  that uses it and re-created if the PID changes (gunicorn fork / preload):
  connections inherited from a parent process are never used or closed
  (closing would also kill the parent's socket) — they are parked.
* Idle connections are KEPT in the pool (up to DB_POOL_MAX) and reused; before
  this every returned connection was closed and each query paid a full
  TCP + TLS + auth handshake.
* Round trips: connections run in AUTOCOMMIT — a single statement costs one
  round trip (no implicit BEGIN / COMMIT / ROLLBACK). Multi-statement writes that
  must be atomic call begin_transaction(conn) and commit explicitly; the
  connection is switched back to autocommit when it is returned to the pool.
* statement_timeout is set once per connection at connect time
  (options=-c statement_timeout=…); if a pooler rejects startup options we
  reconnect without them and run one session-level SET per new connection.
* Pre-ping (SELECT 1) only for a connection idle > DB_PING_IDLE_S (60 s);
  dead ones are discarded. A connection that dies within the idle window fails
  the statement with OperationalError → db_run() retries once on a fresh one.
* connect_timeout + keepalives for the external Postgres.
"""
import os
import threading
import time
from typing import Any, Callable, Optional

import psycopg2
import psycopg2.extras
from psycopg2 import pool

from docubridge_config import DB_URL

DB_CONNECT_TIMEOUT_S = int(os.getenv("DB_CONNECT_TIMEOUT", "10") or 10)
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "15000") or 15000)
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "5") or 5)
DB_PING_IDLE_S = float(os.getenv("DB_PING_IDLE_S", "60") or 60)
RETRYABLE_DB_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

_lock = threading.RLock()
connection_pool = None      # pool of the current process (kept name for compatibility)
_pool_pid: Optional[int] = None
_parked_pools = []          # pools inherited from a parent process: never touched again
_last_used = {}             # id(conn) -> time.monotonic() when returned to the pool
_use_startup_options = True # False after a pooler rejected «options»


def _connect_kwargs() -> dict:
    kw = dict(
        dsn=DB_URL,
        connect_timeout=DB_CONNECT_TIMEOUT_S,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        application_name="docubridge",
    )
    if _use_startup_options:
        kw["options"] = f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
    return kw


def _options_rejected(exc) -> bool:
    msg = str(exc).lower()
    return "options" in msg and ("unsupported" in msg or "startup parameter" in msg or "not supported" in msg)


def _current_pool():
    """Pool for this PID; created lazily, re-created after fork."""
    global connection_pool, _pool_pid
    if not DB_URL:
        return None
    pid = os.getpid()
    with _lock:
        if connection_pool is not None and _pool_pid == pid:
            return connection_pool
        if connection_pool is not None and _pool_pid != pid:
            print(f"[DB] PID changed {_pool_pid} -> {pid}: parking inherited pool, creating a new one")
            _parked_pools.append(connection_pool)
            connection_pool = None
        try:
            connection_pool = pool.ThreadedConnectionPool(0, DB_POOL_MAX, **_connect_kwargs())
            # minconn=0 keeps startup lazy (no connect here), but psycopg2's pool CLOSES
            # every returned connection while len(idle) >= minconn — with 0 that meant
            # a brand-new TCP+TLS+auth handshake for every single query. Keep up to
            # DB_POOL_MAX idle connections instead (minconn is only read in putconn).
            connection_pool.minconn = DB_POOL_MAX
            _pool_pid = pid
            print(f"[DB] Connection pool created (pid {pid}, max {DB_POOL_MAX})")
        except Exception as e:
            connection_pool = None
            _pool_pid = None
            print(f"[DB] Pool creation error: {e}")
        return connection_pool


def init_db_pool():
    """Kept for compatibility: ensure a pool exists for the current process."""
    return _current_pool()


def _ping(conn) -> bool:
    """One-round-trip liveness check (autocommit SELECT 1). False if the connection is dead."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        return True
    except Exception as e:
        print(f"[DB] Dead connection detected: {e}")
        return False


def _init_conn(conn) -> bool:
    """First use of a fresh connection: autocommit (+ session statement_timeout if
    startup options are not supported). No round trip when options were accepted."""
    try:
        conn.autocommit = True
        if not _use_startup_options:
            cur = conn.cursor()
            cur.execute("SET statement_timeout = %s", (DB_STATEMENT_TIMEOUT_MS,))
            cur.close()
        return True
    except Exception as e:
        print(f"[DB] Connection init failed: {e}")
        return False


def begin_transaction(conn):
    """Switch a pooled (autocommit) connection to an explicit transaction for a
    multi-statement atomic write; the caller must commit (or an error rolls back).
    The connection returns to autocommit in return_conn()."""
    try:
        if conn.autocommit is True:
            conn.autocommit = False
    except Exception:
        pass
    return conn


def _restore_autocommit(conn):
    """Leave no open transaction behind and put the connection back to autocommit."""
    if getattr(conn, "autocommit", True) is False:
        try:
            if conn.info.transaction_status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                conn.rollback()
            conn.autocommit = True
        except Exception as e:
            print(f"[DB] restore autocommit failed: {e}")
            return False
    return True


def discard_conn(conn):
    """Close a broken connection instead of returning it to the pool."""
    if not conn:
        return
    _last_used.pop(id(conn), None)
    p = connection_pool if _pool_pid == os.getpid() else None
    try:
        if p is not None:
            p.putconn(conn, close=True)
            return
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def get_conn():
    """A usable connection or None (logged). Fresh connections are initialised
    (autocommit); reused ones are pinged only after DB_PING_IDLE_S of idleness."""
    global _use_startup_options
    if not DB_URL:
        return None
    for attempt in range(1, 4):
        p = _current_pool()
        try:
            conn = p.getconn() if p is not None else psycopg2.connect(**_connect_kwargs())
        except Exception as e:
            if _use_startup_options and _options_rejected(e):
                print("[DB] server/pooler rejected startup options — using session SET statement_timeout")
                _reset_pool_without_options()
                continue
            print(f"[DB] get_conn error (attempt {attempt}/3): {e}")
            continue
        if getattr(conn, "autocommit", None) is not True:   # fresh connection
            if _init_conn(conn):
                return conn
        else:
            last = _last_used.get(id(conn))
            if last is None or time.monotonic() - last <= DB_PING_IDLE_S or _ping(conn):
                return conn
        discard_conn(conn)
    print("[DB] All connection attempts failed")
    return None


def _reset_pool_without_options():
    global _use_startup_options, connection_pool, _pool_pid
    with _lock:
        _use_startup_options = False
        old = connection_pool
        connection_pool, _pool_pid = None, None
    if old is not None:
        try:
            old.closeall()
        except Exception:
            pass


def return_conn(conn):
    if not conn:
        return
    if not _restore_autocommit(conn):
        discard_conn(conn)
        return
    try:
        if connection_pool is not None and _pool_pid == os.getpid():
            _last_used[id(conn)] = time.monotonic()
            connection_pool.putconn(conn)
        else:
            conn.close()
    except Exception as e:
        print(f"[DB] return_conn error: {e}")
        try:
            conn.close()
        except Exception:
            pass


def db_run(label: str, fn: Callable[[Any], Any], default: Any = None, retry: bool = True,
           getconn: Optional[Callable[[], Any]] = None,
           putconn: Optional[Callable[[Any], None]] = None,
           discard: Optional[Callable[[Any], None]] = None) -> Any:
    """Run fn(conn) with a pre-pinged connection; one retry on a fresh connection
    for connection-level errors. Other errors: rollback, log, return default.
    getconn/putconn/discard may be passed by callers (keeps per-module mocking)."""
    getconn = getconn or (lambda: get_conn())
    putconn = putconn or (lambda c: return_conn(c))
    discard = discard or (lambda c: discard_conn(c))
    attempts = 2 if retry else 1
    for attempt in range(1, attempts + 1):
        conn = getconn()
        if not conn:
            print(f"[DB] {label}: no DB connection (attempt {attempt}/{attempts})")
            continue
        try:
            result = fn(conn)
        except RETRYABLE_DB_ERRORS as e:
            print(f"[DB] {label} connection error (attempt {attempt}/{attempts}): {e}")
            discard(conn)
            continue
        except Exception as e:
            print(f"[DB] {label} error: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            putconn(conn)
            return default
        putconn(conn)
        return result
    return default


def ensure_tables() -> bool:
    """Idempotent schema setup. True on success (safe to call again on failure)."""
    if not DB_URL:
        return True

    def _do(conn):
        begin_transaction(conn)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
              id BIGSERIAL PRIMARY KEY,
              chat_id BIGINT NOT NULL,
              user_message TEXT,
              bot_reply TEXT,
              timestamp TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS user_state (
              chat_id BIGINT PRIMARY KEY,
              state TEXT NOT NULL DEFAULT 'greeting',
              data JSONB DEFAULT '{}'::jsonb,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS leads (
              id BIGSERIAL PRIMARY KEY,
              chat_id BIGINT NOT NULL,
              payload JSONB NOT NULL,
              created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS processed_updates (
              update_id BIGINT PRIMARY KEY,
              processed_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS shipments (
              id BIGSERIAL PRIMARY KEY,
              ticket TEXT UNIQUE NOT NULL,
              chat_id BIGINT NOT NULL,
              payload JSONB NOT NULL DEFAULT '{}'::jsonb,
              status TEXT NOT NULL DEFAULT 'accepted',
              created_at TIMESTAMPTZ DEFAULT NOW(),
              updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS start_events (
              id BIGSERIAL PRIMARY KEY,
              chat_id BIGINT NOT NULL,
              payload TEXT,
              username TEXT,
              created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS funnel_reminders (
              chat_id BIGINT PRIMARY KEY,
              state TEXT,
              reminded_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS start_events_time_idx
              ON start_events (created_at);

            CREATE INDEX IF NOT EXISTS idx_processed_updates_time
              ON processed_updates (processed_at);

            CREATE INDEX IF NOT EXISTS chat_history_ts_idx
              ON chat_history (timestamp DESC);

            CREATE INDEX IF NOT EXISTS shipments_ticket_idx
              ON shipments (ticket);

            CREATE INDEX IF NOT EXISTS shipments_chat_idx
              ON shipments (chat_id);
            """
        )
        # Optional ticket column on leads (idempotent)
        cur.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='leads' AND column_name='ticket'
              ) THEN
                ALTER TABLE leads ADD COLUMN ticket TEXT;
              END IF;
            END $$;
            """
        )
        # Tracking: shipment_events + updated_at on shipments (idempotent)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shipment_events (
              id BIGSERIAL PRIMARY KEY,
              ticket TEXT NOT NULL,
              status TEXT NOT NULL,
              comment TEXT,
              created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS shipment_events_ticket_idx
              ON shipment_events (ticket, created_at);
            ALTER TABLE shipments
              ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
            -- Google Sheet outbox: synced = row is in the sheet; claimed = lease while exporting
            ALTER TABLE shipments ADD COLUMN IF NOT EXISTS sheet_synced_at TIMESTAMPTZ;
            ALTER TABLE shipments ADD COLUMN IF NOT EXISTS sheet_claimed_at TIMESTAMPTZ;
            -- currency the client chose for payment after the order (bot autonomy)
            ALTER TABLE shipments ADD COLUMN IF NOT EXISTS pay_currency TEXT;
            ALTER TABLE shipments ADD COLUMN IF NOT EXISTS pay_currency_at TIMESTAMPTZ;
            -- client accepted the terms → the manager sends the shipping address
            ALTER TABLE shipments ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;
            ALTER TABLE shipments ADD COLUMN IF NOT EXISTS pay_requested_at TIMESTAMPTZ;
            -- One-off admin broadcasts: one row per (campaign, chat) = never sent twice
            CREATE TABLE IF NOT EXISTS broadcast_log (
                campaign TEXT NOT NULL,
                chat_id BIGINT NOT NULL,
                segment TEXT,
                status TEXT NOT NULL DEFAULT 'sending',
                error TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                sent_at TIMESTAMPTZ,
                PRIMARY KEY (campaign, chat_id)
            );
            """
        )
        conn.commit()
        cur.close()
        return True

    ok = bool(db_run("ensure_tables", _do, default=False,
                     getconn=lambda: get_conn(), putconn=lambda c: return_conn(c)))
    print("[DB] ensure_tables OK" if ok else "[DB] ensure_tables FAILED (will retry later)")
    return ok
