# -*- coding: utf-8 -*-
"""DocuBridge DB pool part B — Phase 2."""
from typing import Optional
import psycopg2
from docubridge_config import DB_URL
from docubridge_db_pool_a import get_conn, return_conn

def is_update_processed(update_id: int) -> bool:
    conn = None
    if not DB_URL:
        return False
    try:
        conn = get_conn()
        if not conn:
            return False
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM processed_updates WHERE update_id = %s",
            (update_id,),
        )
        exists = cur.fetchone() is not None
        cur.close()
        return exists
    except Exception as e:
        print(f"[DB] is_update_processed error: {e}")
        return False
    finally:
        if conn:
            return_conn(conn)


def mark_update_processed(update_id: int):
    conn = None
    if not DB_URL:
        return
    try:
        conn = get_conn()
        if not conn:
            print("[DB] mark_update_processed: Failed to get connection")
            return
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO processed_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (update_id,),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] mark_update_processed error: {e}")
    finally:
        if conn:
            return_conn(conn)


def cleanup_old_updates():
    conn = None
    if not DB_URL:
        return
    try:
        conn = get_conn()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM processed_updates
            WHERE processed_at < NOW() - INTERVAL '7 days'
            """
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        print(f"[DB] Cleaned up {deleted} old update records")
    except Exception as e:
        print(f"[DB] cleanup_old_updates error: {e}")
    finally:
        if conn:
            return_conn(conn)


def save_message(chat_id: int, user_text: Optional[str], bot_reply: Optional[str]):
    conn = None
    try:
        if DB_URL:
            conn = get_conn()
            if not conn:
                return
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO chat_history (chat_id, user_message, bot_reply)
                VALUES (%s, %s, %s)
                """,
                (int(chat_id), user_text, bot_reply),
            )
            conn.commit()
            cur.close()
    except Exception as e:
        print(f"[DB] save_message error: {e}")
    finally:
        if conn:
            return_conn(conn)
