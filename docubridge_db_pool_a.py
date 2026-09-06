# -*- coding: utf-8 -*-
"""DocuBridge DB pool and persistence — Phase 2."""
import json
from typing import Optional, Dict, Tuple

import psycopg2
import psycopg2.extras
from psycopg2 import pool

from docubridge_config import DB_URL
from docubridge_helpers import generate_ticket_candidate

# ------------ DB Connection Pool ------------
connection_pool = None


def init_db_pool():
    global connection_pool
    if not DB_URL:
        return
    try:
        connection_pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DB_URL,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        print("[DB] Connection pool created")
    except Exception as e:
        print(f"[DB] Pool creation error: {e}")


def get_conn():
    if not DB_URL:
        return None
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if not connection_pool:
                return psycopg2.connect(
                    DB_URL,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )
            conn = connection_pool.getconn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return conn
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as db_err:
                print(f"[DB] Dead connection detected: {db_err}")
                try:
                    connection_pool.putconn(conn, close=True)
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    continue
                raise
        except Exception as e:
            print(f"[DB] get_conn error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                print("[DB] All connection attempts failed")
                return None
    return None


def return_conn(conn):
    if not conn:
        return
    try:
        if connection_pool:
            connection_pool.putconn(conn)
        else:
            conn.close()
    except Exception as e:
        print(f"[DB] return_conn error: {e}")
        try:
            conn.close()
        except Exception:
            pass


def ensure_tables():
    conn = None
    if not DB_URL:
        return
    try:
        conn = get_conn()
        if not conn:
            print("[DB] ensure_tables: Failed to get connection")
            return
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
        conn.commit()
        cur.close()
        print("[DB] ensure_tables OK")
    except Exception as e:
        print(f"[DB] ensure_tables error: {e}")
    finally:
        if conn:
            return_conn(conn)
