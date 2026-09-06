# -*- coding: utf-8 -*-
"""DocuBridge env config — Phase 2."""
import os
from dotenv import load_dotenv

load_dotenv()

SKIP_STARTUP = os.getenv("DOCUBRIDGE_SKIP_STARTUP", "").strip() in {"1", "true", "yes"}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN and not SKIP_STARTUP:
    print("ERROR: TELEGRAM_BOT_TOKEN not set")
    raise SystemExit(1)
if not BOT_TOKEN:
    BOT_TOKEN = "000000000:TEST_TOKEN_FOR_UNIT_TESTS"

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL and not SKIP_STARTUP:
    print("WARNING: DATABASE_URL не задан — сохранение истории отключено")

WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
PORT = int(os.getenv("PORT", "5000"))

XAI_API_KEY = os.getenv("XAI_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
MANAGER_CONTACT = os.getenv("MANAGER_CONTACT", "@DocuBridgeSupport")
