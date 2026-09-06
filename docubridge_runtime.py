# -*- coding: utf-8 -*-
"""DocuBridge runtime: Flask app, bot, AI — Phase 2."""
from flask import Flask
import telebot
from openai import OpenAI

from docubridge_config import (
    SKIP_STARTUP,
    BOT_TOKEN,
    DB_URL,
    WEBHOOK_BASE,
    WEBHOOK_SECRET,
    PORT,
    XAI_API_KEY,
    ADMIN_CHAT_ID,
    MANAGER_CONTACT,
)
from docubridge_helpers import *  # noqa: F401,F403
from docubridge_db import *  # noqa: F401,F403

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

client = None
if XAI_API_KEY:
    client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
    print("[xAI] client is ON (model grok-4.5)")
else:
    print("[xAI] client is OFF (XAI_API_KEY not set)")

print(f"[ADMIN] Admin ID: {ADMIN_CHAT_ID or '— (не задан)'}")

# ------------ xAI ------------

def ai_reply(text: str) -> str:
    if not client:
        return (
            "Сейчас умные ответы временно недоступны. "
            "Воспользуйтесь меню или напишите менеджеру."
        )
    try:
        r = client.chat.completions.create(
            model="grok-4.5",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты вежливый логист-ассистент DocuBridge. "
                        "Отвечай по делу и кратко, на русском. "
                        "Маршруты только UA↔RU и UA↔BY. "
                        "Паспорта и ID оригиналы не принимаем. "
                        "Оценка от €95 за конверт до 0,5 кг; срок ориентир 10–14 раб. дней."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.6,
            max_tokens=500,
            timeout=30,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[xAI] error: {e}")
        return "Небольшая пауза на стороне ИИ. Попробуйте ещё раз или выберите пункт меню."
