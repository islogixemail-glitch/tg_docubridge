import os
import psycopg2
import json
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, Update

# ====== ENV ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set")
    raise SystemExit(1)
load_dotenv()  # подтянет DATABASE_URL из .env локально (на Render надо будет задать переменную окружения)
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    print("WARNING: DATABASE_URL не задан — сохранение истории отключено")

WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
PORT = int(os.getenv("PORT", "5000"))

# ====== TELEBOT ======
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)  # threaded=False — стабильно в webhook-режиме

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton('/consult'), KeyboardButton('/ua_ru'))
    markup.add(KeyboardButton('/eu_ua'), KeyboardButton('/news'))
    return markup
    
def save_message(chat_id: int, user_text: str | None, bot_reply: str | None):
    """Сохраняет одну запись диалога в таблицу chat_history (Neon). Молча пропускает, если DB_URL не задан."""
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_history (chat_id, user_message, bot_reply) VALUES (%s, %s, %s)",
            (int(chat_id), user_text, bot_reply)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        # без падения бота — просто лог
        print(f"[DB] save_message error: {e}")
def get_state(chat_id: int) -> tuple[str, dict]:
    """Вернёт (state, data_dict). Если записи нет — создаст со state='greeting' и пустыми данными."""
    if not DB_URL:
        return "greeting", {}
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT state, data FROM user_state WHERE chat_id=%s;", (int(chat_id),))
        row = cur.fetchone()
        if row is None:
            # создать дефолтную запись
            cur.execute(
                "INSERT INTO user_state (chat_id, state, data) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO NOTHING;",
                (int(chat_id), "greeting", psycopg2.extras.Json({}))
            )
            conn.commit()
            result = ("greeting", {})
        else:
            result = (row["state"], row["data"] if row["data"] is not None else {})
        cur.close(); conn.close()
        return result
    except Exception as e:
        print(f"[DB] get_state error: {e}")
        return "greeting", {}

def set_state(chat_id: int, state: str) -> None:
    """Установит новое состояние пользователя и обновит updated_at."""
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_state (chat_id, state, data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET state=EXCLUDED.state, updated_at=NOW();
        """, (int(chat_id), state, psycopg2.extras.Json({})))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] set_state error: {e}")

def update_data(chat_id: int, patch: dict) -> None:
    """Сольёт patch в поле data (JSONB) и обновит updated_at."""
    if not DB_URL:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_state (chat_id, state, data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE
            SET data = COALESCE(user_state.data, '{}'::jsonb) || EXCLUDED.data,
                updated_at = NOW();
        """, (int(chat_id), "greeting", psycopg2.extras.Json(patch)))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] update_data error: {e}")


@bot.message_handler(commands=['start'])
def start(message):
    print(f"[BOT] received /start from {message.chat.id}")
    reply = (
        "Добро пожаловать в IS-Logix Bot! 😊\n"
        "Мы помогаем с доставкой документов между Украиной, Россией, Беларусью и Европой, несмотря на сложности.\n"
        "Выберите опцию:"
    )
    save_message(message.chat.id, "/start", reply)
    bot.send_message(message.chat.id, reply, reply_markup=main_menu())


@bot.message_handler(commands=['consult'])
def consult(message):
    print(f"[BOT] received /consult from {message.chat.id}")
    reply = ("Расскажите о вашем запросе: какой документ, откуда и куда? "
             "(Например: 'Доверенность из Киева в Москву')")
    save_message(message.chat.id, "/consult", reply)
    bot.send_message(message.chat.id, reply)
    bot.register_next_step_handler(message, save_lead)


@bot.message_handler(commands=['ua_ru'])
def ua_ru(message):
    print(f"[BOT] received /ua_ru from {message.chat.id}")
    reply = (
        "Доставка из Украины в Россию: Несмотря на ситуацию, помогаем с доставкой различных документов. "
        "Есть некоторые ограничения. Гайд: https://www.is-logix.com/section/novosti/.\n"
        "Нужна помощь? /consult"
    )
    save_message(message.chat.id, "/ua_ru", reply)
    bot.send_message(message.chat.id, reply)


@bot.message_handler(commands=['eu_ua'])
def eu_ua(message):
    print(f"[BOT] received /eu_ua from {message.chat.id}")
    reply = (
        "Доставка документов из Европы в Украину: Визы, сертификаты, безопасно. Подробности: "
        "https://www.is-logix.com/section/novosti/.\nКонсультация: /consult"
    )
    save_message(message.chat.id, "/eu_ua", reply)
    bot.send_message(message.chat.id, reply)

@bot.message_handler(commands=['news'])
def news(message):
    print(f"[BOT] received /news from {message.chat.id}")
    reply = (
        "Последние новости по логистике: Изменения в санкциях 2025 "
        "(https://www.is-logix.com/section/novosti/). "
        "Подписывайтесь на канал: https://t.me/DocuBridgeInfo"
    )
    save_message(message.chat.id, "/news", reply)
    bot.send_message(message.chat.id, reply)

@bot.message_handler(func=lambda m: True)
def fallback(message):
    print(f"[BOT] fallback from {message.chat.id}: {message.text}")
    reply = "Не понял. Выберите команду из меню."
    save_message(message.chat.id, message.text, reply)
    bot.send_message(message.chat.id, reply, reply_markup=main_menu())

# ====== FLASK APP ======
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify(status="ok", service="is-logix-bot")

# --- Основной вебхук (используем строку для Update.de_json) ---
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook_secret():
    try:
        json_str = request.get_data(cache=False, as_text=True)
        print(">>> GOT UPDATE (secret):", json_str)
        update = Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        import traceback
        print("Webhook SECRET error:", repr(e))
        traceback.print_exc()
    return "OK", 200

# --- Резервный путь без секрета ---
@app.route("/webhook", methods=["POST"])
def webhook_fallback():
    try:
        json_str = request.get_data(cache=False, as_text=True)
        print(">>> GOT UPDATE (fallback):", json_str)
        update = Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        import traceback
        print("Webhook FALLBACK error:", repr(e))
        traceback.print_exc()
    return "OK", 200

def ensure_webhook():
    if not WEBHOOK_BASE:
        print("WEBHOOK_BASE not set — пропускаю setWebhook.")
        return
    webhook_url = f"{WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}"
    try:
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        print(f"Webhook set to: {webhook_url}")
    except Exception as e:
        print(f"Failed to set webhook: {e}")

ensure_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)



