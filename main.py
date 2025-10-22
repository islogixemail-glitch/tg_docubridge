# main.py — DocuBridge Bot (Flask + TeleBot + OpenAI + Postgres)
# Готов к деплою на Render с командой: gunicorn main:app --timeout 120

import os
import json
import traceback
from datetime import datetime

from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ВАЖНО: грузим .env ДО чтения переменных
load_dotenv()

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, Update

import psycopg2
import psycopg2.extras

from openai import OpenAI

# ---------- ENV ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set")
    raise SystemExit(1)

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    print("WARNING: DATABASE_URL не задан — сохранение истории отключено")

WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")  # напр.: https://tg-docubridge.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
PORT = int(os.getenv("PORT", "5000"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY не задан — умные ответы отключены")

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))  # ваш личный chat_id для уведомлений

# ---------- App / Bot / OpenAI ----------
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
print(f"[OpenAI] client is {'ON' if client else 'OFF'}")

# ---------- DB helpers ----------
def get_conn():
    if not DB_URL:
        return None
    return psycopg2.connect(DB_URL)

def ensure_tables():
    if not DB_URL:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
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
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("[DB] ensure_tables OK")
    except Exception as e:
        print(f"[DB] ensure_tables error: {e}")

def save_message(chat_id: int, user_text: str | None, bot_reply: str | None):
    # Пишем историю и дублируем админу
    try:
        if DB_URL:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO chat_history (chat_id, user_message, bot_reply)
                VALUES (%s, %s, %s)
            """, (int(chat_id), user_text, bot_reply))
            conn.commit()
            cur.close()
            conn.close()
    except Exception as e:
        print(f"[DB] save_message error: {e}")

    # Уведомление администратору (короткий лог диалога)
    try:
        if ADMIN_CHAT_ID:
            u = f"👤{chat_id}: {user_text}" if user_text else None
            b = f"🤖Bot: {bot_reply}" if bot_reply else None
            lines = [l for l in [u, b] if l]
            if lines:
                bot.send_message(ADMIN_CHAT_ID, "\n".join(lines))
    except Exception as e:
        print(f"[ADMIN notify] save_message notify error: {e}")

def get_state(chat_id: int):
    try:
        if not DB_URL:
            return ("greeting", {})
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT state, data FROM user_state WHERE chat_id=%s", (int(chat_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return row["state"], (row["data"] or {})
        return ("greeting", {})
    except Exception as e:
        print(f"[DB] get_state error: {e}")
        return ("greeting", {})

def set_state(chat_id: int, state: str):
    try:
        if not DB_URL:
            return
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_state (chat_id, state, data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE
            SET state=EXCLUDED.state, updated_at=NOW()
        """, (int(chat_id), state, json.dumps({})))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] set_state error: {e}")

def update_data(chat_id: int, new_data: dict):
    try:
        if not DB_URL:
            return
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_state (chat_id, state, data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE
            SET data=EXCLUDED.data, updated_at=NOW()
        """, (int(chat_id), "collecting", json.dumps(new_data)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] update_data error: {e}")

# ---------- OpenAI logic ----------
def generate_chatgpt_response(user_message: str, chat_id: int) -> str:
    if not client:
        return "Сейчас умные ответы временно недоступны. Напишите ваш вопрос — менеджер подключится и поможет."
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Ты ассистент по логистике документов между Украиной, Россией, Беларусью и Европой. "
                    "Отвечай кратко, профессионально, на русском, дружелюбным тоном. "
                    "Если вопрос о доставке документов — давай чёткие шаги, сроки и напоминания об ограничениях (без паспортов/ценностей/товаров)."
                )},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=600
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"[OpenAI] error: {e}")
        return "Извините, временная техническая пауза. Попробуйте ещё раз через минуту."

REQUIRED_FIELDS = [
    "doc_type",
    "from_country", "from_city",
    "to_country", "to_city",
    "pages_a4", "weight_grams",
    "urgency",
    "name", "phone", "email", "best_time"
]

def calc_weight_if_needed(d: dict) -> dict:
    try:
        pages = int(d.get("pages_a4") or 0)
    except:
        pages = 0
    w = d.get("weight_grams")
    if (not w or int(w) == 0) and pages > 0:
        # грубо: ~6 г/лист, округление к кратному 6 г
        d["weight_grams"] = int((pages * 6 + 5) // 6 * 6)
    return d

def normalize_and_validate(d: dict) -> tuple[dict, list]:
    errors = []
    # Страны — только Украина / Россия / Беларусь
    allowed_countries = {"Украина", "Россия", "Беларусь"}
    for k in ["from_country", "to_country"]:
        v = (str(d.get(k) or "")).strip().title()
        if v and v not in allowed_countries:
            errors.append(f"{k}: недопустимая страна")
            d[k] = ""
        else:
            d[k] = v

    # Телефон — только +380 / +7 / +375
    phone = (str(d.get("phone") or "")).strip()
    if phone and not (phone.startswith("+380") or phone.startswith("+7") or phone.startswith("+375")):
        errors.append("phone: формат должен начинаться с +380 / +7 / +375")
        d["phone"] = ""

    # Запрещённые типы (паспорт/товары/ценности/деньги)
    bad_keywords = ["паспорт", "passport", "товар", "деньги", "валю", "ценн"]
    doc = (str(d.get("doc_type") or "")).lower()
    if any(b in doc for b in bad_keywords):
        errors.append("doc_type: недопустимый тип (паспорт/товары/ценности)")
        d["doc_type"] = ""

    # Автоподсчёт веса по листам
    d = calc_weight_if_needed(d)
    return d, errors

def is_complete(d: dict) -> bool:
    for k in REQUIRED_FIELDS:
        if k not in d or d[k] in (None, "", 0):
            return False
    return True

def extract_fields_via_openai(text: str, current_data: dict) -> dict:
    """Просим OpenAI вернуть ТОЛЬКО JSON с нужными ключами."""
    if not client:
        return {}
    try:
        sys = (
            "Верни ТОЛЬКО JSON с ключами:\n"
            "{\n"
            '  "doc_type": "",\n'
            '  "from_country": "", "from_city": "",\n'
            '  "to_country": "",   "to_city": "",\n'
            '  "pages_a4": 0, "weight_grams": 0,\n'
            '  "urgency": "",\n'
            '  "name": "", "phone": "", "email": "",\n'
            '  "best_time": ""\n'
            "}\n"
            "Правила:\n"
            "- Страны: только Украина/Россия/Беларусь (иначе оставь пусто).\n"
            "- НЕ паспорта/товары/деньги/ценности — такие значения оставь пустыми.\n"
            "- Телефон: только +380 / +7 / +375 — иначе пусто.\n"
            "- Если pages_a4 > 0 и weight_grams == 0 → weight_grams ≈ pages_a4*6.\n"
            "Верни только JSON без текста вокруг."
        )
        user = (
            f"UserMsg: {text}\n"
            "Current state:\n" +
            "\n".join([f"{k}={current_data.get(k)}" for k in REQUIRED_FIELDS])
        )
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=400
        )
        raw = r.choices[0].message.content.strip()
        try:
            parsed = json.loads(raw)
        except Exception:
            s = raw.find("{"); e = raw.rfind("}")
            parsed = json.loads(raw[s:e+1]) if s >= 0 and e >= 0 else {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"[OpenAI extract] error: {e}")
        return {}

def notify_admin_lead(chat_id: int, payload: dict):
    if not ADMIN_CHAT_ID:
        return
    try:
        summary_lines = [
            "🟢 *Новый лид (DocuBridge)*",
            f"Chat ID: `{chat_id}`",
            "",
            f"Тип документа: {payload.get('doc_type') or '—'}",
            f"Маршрут: {payload.get('from_country')}/{payload.get('from_city')} → "
            f"{payload.get('to_country')}/{payload.get('to_city')}",
            f"Листов A4: {payload.get('pages_a4') or 0}, вес ≈ {payload.get('weight_grams') or 0} г",
            f"Срочность: {payload.get('urgency') or '—'}",
            "",
            f"Имя: {payload.get('name') or '—'}",
            f"Телефон: {payload.get('phone') or '—'}",
            f"Email: {payload.get('email') or '—'}",
            f"Лучшее время связи: {payload.get('best_time') or '—'}",
        ]
        bot.send_message(ADMIN_CHAT_ID, "\n".join(summary_lines), parse_mode="Markdown")
    except Exception as e:
        print(f"[ADMIN notify] lead notify error: {e}")

# ---------- UI ----------
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("/consult"))
    kb.add(KeyboardButton("/news"))
    return kb

# ---------- Handlers ----------
@bot.message_handler(commands=['start'])
def start(message):
    reply = (
        "Добро пожаловать в IS-Logix DocuBridge! 🇸🇰📄\n"
        "Поможем с пересылкой документов между Украиной, Россией и Беларусью через Словакию.\n\n"
        "Нажмите /consult чтобы начать расчёт и оформление заявки."
    )
    save_message(message.chat.id, "/start", reply)
    bot.send_message(message.chat.id, reply, reply_markup=main_menu())

@bot.message_handler(commands=['consult'])
def consult(message):
    set_state(message.chat.id, "collecting")
    q = "Начнём оформление 📋\nКоротко опишите задачу: тип документа и маршрут (откуда → куда)."
    save_message(message.chat.id, "/consult", q)
    bot.send_message(message.chat.id, q)

@bot.message_handler(commands=['news'])
def news(message):
    reply = (
        "Последние новости по логистике и пересылке документов: "
        "https://t.me/DocuBridgeInfo\n"
        "Есть вопросы по доставке вашего кейса? Напишите сюда, подскажу."
    )
    save_message(message.chat.id, "/news", reply)
    bot.send_message(message.chat.id, reply, reply_markup=main_menu())

# Тест OpenAI: мгновенная проверка
@bot.message_handler(commands=['ai'])
def ai_ping(message):
    reply = generate_chatgpt_response("Ответь одним словом: OK", message.chat.id)
    save_message(message.chat.id, "/ai", reply)
    bot.send_message(message.chat.id, f"AI: {reply}")

# Универсальный обработчик: сбор данных или умный ответ
@bot.message_handler(func=lambda m: True)
def fallback(message):
    print(f"[BOT] fallback from {message.chat.id}: {message.text}")
    state, data = get_state(message.chat.id)
    user_text = (message.text or "").strip()

    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except:
        pass

    if state == "collecting":
        # 1) Экстракция и слияние
        extracted = extract_fields_via_openai(user_text, data)
        merged = {**(data or {}), **(extracted or {})}
        # 2) Нормализация/валидация
        merged, val_errors = normalize_and_validate(merged)
        update_data(message.chat.id, merged)

        # 3) Готов комплект?
        if is_complete(merged) and not val_errors:
            # сохранить лид
            try:
                if DB_URL:
                    conn = get_conn()
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO leads (chat_id, payload) VALUES (%s, %s)",
                        (int(message.chat.id), psycopg2.extras.Json(merged))
                    )
                    conn.commit()
                    cur.close()
                    conn.close()
            except Exception as e:
                print(f"[DB] INSERT lead error: {e}")

            # уведомить администратора
            notify_admin_lead(message.chat.id, merged)

            reply = (
                "✅ Спасибо! Все данные получены.\n"
                f"Маршрут: {merged.get('from_city')}, {merged.get('from_country')} → "
                f"{merged.get('to_city')}, {merged.get('to_country')}\n"
                f"Листов A4: {merged.get('pages_a4')} (≈ {merged.get('weight_grams')} г)\n"
                f"Связаться: {merged.get('name')}, {merged.get('phone')}, {merged.get('email')} "
                f"({merged.get('best_time')})\n\n"
                "Наш менеджер свяжется с вами в ближайшее время. Если нужно что-то изменить — просто напишите."
            )
            save_message(message.chat.id, user_text, reply)
            bot.send_message(message.chat.id, reply, reply_markup=main_menu())
            set_state(message.chat.id, "completed")
            return

        # 4) Ещё не всё — спрашиваем следующее поле
        questions = {
            "doc_type": "Какой тип документа? (например: доверенность, диплом, свидетельство)",
            "from_country": "Из какой страны отправляем? (Украина/Россия/Беларусь)",
            "from_city": "Из какого города отправляем?",
            "to_country": "В какую страну доставляем? (Украина/Россия/Беларусь)",
            "to_city": "В какой город доставляем?",
            "pages_a4": "Сколько листов A4? (число)",
            "weight_grams": "Если знаете точный вес в граммах — укажите, иначе оставим по расчёту.",
            "urgency": "Срочность: обычная или срочная?",
            "name": "Как к вам обращаться (имя/фамилия)?",
            "phone": "Контактный телефон (+380 / +7 / +375):",
            "email": "Электронная почта:",
            "best_time": "Когда вам удобнее принимать звонок/сообщение?"
        }
        for key in REQUIRED_FIELDS:
            if not merged.get(key):
                q = questions[key]
                save_message(message.chat.id, user_text, q)
                bot.send_message(message.chat.id, q)
                return

        # Если здесь — поля заполнены, но есть ошибки валидации
        if val_errors:
            q = "Обнаружены ошибки: " + "; ".join(val_errors) + ". Уточните, пожалуйста."
            save_message(message.chat.id, user_text, q)
            bot.send_message(message.chat.id, q)
            return

    # --- не режим сбора: обычный ответ GPT ---
    reply = generate_chatgpt_response(user_text, message.chat.id)
    save_message(message.chat.id, user_text, reply)
    bot.send_message(message.chat.id, reply, reply_markup=main_menu())

# ---------- Webhook ----------
@app.route("/", methods=["GET"])
def index():
    return "OK", 200

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    try:
        if request.headers.get("content-type") == "application/json":
            json_str = request.get_data().decode("utf-8")
            update = Update.de_json(json.loads(json_str))
            bot.process_new_updates([update])
        else:
            print("[Webhook] Unsupported content-type")
    except Exception as e:
        print("[Webhook] error:", e)
        traceback.print_exc()
    return "OK", 200

def ensure_webhook():
    try:
        if not WEBHOOK_BASE:
            print("WARNING: WEBHOOK_BASE не задан — вебхук не будет выставлен")
            return
        url = f"{WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}"
        bot.remove_webhook()
        ok = bot.set_webhook(url=url, drop_pending_updates=True)
        if ok:
            print(f"Webhook set to: {url}")
        else:
            print("ERROR: set_webhook returned False")
    except Exception as e:
        print(f"[Webhook] set error: {e}")

# ---------- Entrypoint ----------
ensure_tables()
ensure_webhook()

if __name__ == "__main__":
    # Локальный запуск (для тестов): python main.py
    app.run(host="0.0.0.0", port=PORT, debug=False)


