# -*- coding: utf-8 -*-
"""DocuBridge tracking persistence: admin lists, status updates, events."""
from typing import Optional, Dict, List

import psycopg2
import psycopg2.extras

from docubridge_config import DB_URL
from docubridge_db_pool_a import get_conn, return_conn


def list_recent_shipments(limit: int = 20) -> List[Dict]:
    if not DB_URL:
        return []
    conn = None
    try:
        conn = get_conn()
        if not conn:
            return []
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT ticket, chat_id, payload, status, created_at, updated_at
              FROM shipments
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (int(limit),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows
    except Exception as e:
        print(f"[DB] list_recent_shipments error: {e}")
        return []
    finally:
        if conn:
            return_conn(conn)


def update_shipment_status(ticket: str, status: str, comment: Optional[str] = None) -> Optional[Dict]:
    """UPDATE status/updated_at + insert event. Returns updated row or None."""
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
            UPDATE shipments SET status = %s, updated_at = NOW()
             WHERE UPPER(ticket) = UPPER(%s)
            RETURNING ticket, chat_id, payload, status, created_at, updated_at
            """,
            (status, ticket.strip()),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return None
        cur.execute(
            "INSERT INTO shipment_events (ticket, status, comment) VALUES (%s, %s, %s)",
            (row["ticket"], status, comment),
        )
        conn.commit()
        cur.close()
        return dict(row)
    except Exception as e:
        print(f"[DB] update_shipment_status error: {e}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return None
    finally:
        if conn:
            return_conn(conn)


def get_shipment_events(ticket: str) -> List[Dict]:
    if not DB_URL:
        return []
    conn = None
    try:
        conn = get_conn()
        if not conn:
            return []
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT ticket, status, comment, created_at
              FROM shipment_events
             WHERE UPPER(ticket) = UPPER(%s)
             ORDER BY created_at ASC, id ASC
            """,
            (ticket.strip(),),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows
    except Exception as e:
        print(f"[DB] get_shipment_events error: {e}")
        return []
    finally:
        if conn:
            return_conn(conn)
