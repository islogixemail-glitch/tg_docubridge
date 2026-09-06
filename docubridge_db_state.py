# -*- coding: utf-8 -*-
"""DocuBridge DB state/shipments — Phase 2."""
import json
from typing import Optional, Dict, Tuple

import psycopg2
import psycopg2.extras

from docubridge_config import DB_URL
from docubridge_helpers import generate_ticket_candidate
from docubridge_db_pool import get_conn, return_conn

def get_state(chat_id: int) -> Tuple[str, Dict]:
    conn = None
    try:
        if not DB_URL:
            return ("greeting", {})
        conn = get_conn()
        if not conn:
            return ("greeting", {})
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT state, data FROM user_state WHERE chat_id = %s", (int(chat_id),))
        row = cur.fetchone()
        cur.close()
        return (row["state"], row["data"] or {}) if row else ("greeting", {})
    except Exception as e:
        print(f"[DB] get_state error: {e}")
        return ("greeting", {})
    finally:
        if conn:
            return_conn(conn)


def set_state(chat_id: int, state: str, data: Optional[Dict] = None):
    conn = None
    try:
        if not DB_URL:
            return
        conn = get_conn()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_state (chat_id, state, data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE
              SET state = EXCLUDED.state,
                  data  = COALESCE(EXCLUDED.data, user_state.data),
                  updated_at = NOW()
            """,
            (int(chat_id), state, json.dumps(data or {}, ensure_ascii=False)),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] set_state error: {e}")
    finally:
        if conn:
            return_conn(conn)


def update_data(chat_id: int, new_data: Dict):
    conn = None
    try:
        if not DB_URL:
            return
        conn = get_conn()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE user_state
               SET data = %s, updated_at = NOW()
             WHERE chat_id = %s
            """,
            (json.dumps(new_data, ensure_ascii=False), int(chat_id)),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] update_data error: {e}")
    finally:
        if conn:
            return_conn(conn)


def create_shipment(chat_id: int, payload: Dict) -> Optional[str]:
    """Insert shipment + lead; return ticket or None."""
    if not DB_URL:
        # Offline fallback ticket (still unique enough for demo)
        ticket = generate_ticket_candidate()
        payload["ticket"] = ticket
        return ticket

    conn = None
    try:
        conn = get_conn()
        if not conn:
            ticket = generate_ticket_candidate()
            payload["ticket"] = ticket
            return ticket

        cur = conn.cursor()
        ticket = None
        for _ in range(12):
            candidate = generate_ticket_candidate()
            try:
                cur.execute(
                    """
                    INSERT INTO shipments (ticket, chat_id, payload, status)
                    VALUES (%s, %s, %s, 'accepted')
                    """,
                    (candidate, int(chat_id), psycopg2.extras.Json(payload)),
                )
                ticket = candidate
                break
            except psycopg2.IntegrityError:
                conn.rollback()
                continue

        if not ticket:
            cur.close()
            return None

        payload = dict(payload)
        payload["ticket"] = ticket
        cur.execute(
            """
            INSERT INTO leads (chat_id, payload, ticket)
            VALUES (%s, %s, %s)
            """,
            (int(chat_id), psycopg2.extras.Json(payload), ticket),
        )
        conn.commit()
        cur.close()
        return ticket
    except Exception as e:
        print(f"[DB] create_shipment error: {e}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return None
    finally:
        if conn:
            return_conn(conn)


def get_shipment_by_ticket(ticket: str) -> Optional[Dict]:
    if not DB_URL:
        return None
    conn = None
    try:
        conn = get_conn()
        if not conn:
            return None
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT ticket, chat_id, payload, status, created_at, updated_at
              FROM shipments
             WHERE UPPER(ticket) = UPPER(%s)
            """,
            (ticket.strip(),),
        )
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[DB] get_shipment_by_ticket error: {e}")
        return None
    finally:
        if conn:
            return_conn(conn)
