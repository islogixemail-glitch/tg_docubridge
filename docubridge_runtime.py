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
    WEBHOOK_SECRET_TOKEN,
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

AI_FAIL_PREFIXES = ("ошибка", "error", "небольшая пауза", "сейчас умные")
AI_UNKNOWN_MARK = "[менеджер]"

AI_SYSTEM_PROMPT = (
    "Ты ассистент сервиса DocuBridge (доставка бумажных документов). Отвечай кратко, "
    "ТОЛЬКО на основе фактов ниже. Ничего не придумывай.\n"
    "Язык ответа = язык сообщения клиента: пишет по-украински — отвечай по-украински, "
    "иначе по-русски.\n"
    "Факты:\n"
    "- Маршруты: Украина → Россия, Украина → Беларусь, Россия → Украина, Беларусь → Украина. Других нет.\n"
    f"- Цена: €{PRICE_ORDINARY_EUR} за конверт до {ENVELOPE_MAX_G} г. Тяжелее 0,5 кг — по согласованию с менеджером.\n"
    f"- Срок: ориентировочно {ETA_RANGE} рабочих дней.\n"
    "- Не принимаем: паспорта, ID-карты, военные билеты и другие документы, удостоверяющие личность.\n"
    f"- Принимаем: {DOC_TYPES_HINT}; прочие бумажные документы в конверте до 0,5 кг.\n"
    "- Расчёт и заявка: /start; отслеживание: /track и номер DBxxxx.\n"
    "Правила:\n"
    "- Скидок, акций и других цен не обещай.\n"
    "- Оплата: 100% предоплата после поступления документов (из Украины — в Братиславу, "
    "из России и Беларуси — нашему агенту в Москве). Реквизиты для оплаты бот присылает только после поступления документов "
    "(о поступлении сообщает менеджер); до этого реквизитов нет.\n"
    "- Передача: документы клиент отправляет своими силами и за свой счёт — из Украины в Словакию "
    "(Новой Почтой или другим способом), из России и Беларуси в Москву (например, СДЭКом). "
    "Адрес даёт менеджер после заявки и подтверждения условий.\n"
    "- Не называй адреса, реквизиты, суммы в других валютах, курсы, другие сроки, стоимость "
    "пересылки, законы, таможенные и прочие ограничения — их в фактах нет.\n"
    f"- Если ответа в фактах нет, ответь ровно: {AI_UNKNOWN_MARK}"
)


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
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            max_tokens=400,
            timeout=30,
        )
        out = (r.choices[0].message.content or "").strip()
        if not out or AI_UNKNOWN_MARK in out:
            return "Сейчас умные ответы не знают ответа на этот вопрос."
        return out
    except Exception as e:
        print(f"[xAI] error: {e}")
        return "Небольшая пауза на стороне ИИ. Попробуйте ещё раз или выберите пункт меню."
