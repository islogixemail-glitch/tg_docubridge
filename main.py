import os
from flask import Flask, request, jsonify
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, Update

# ====== ENV ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set")
    raise SystemExit(1)

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

@bot.message_handler(commands=['start'])
def start(message):
    print(f"[BOT] received /start from {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "Добро пожаловать в IS-Logix Bot! 😊\n"
        "Мы помогаем с доставкой документов между Украиной, Россией, Беларусью и Европой, несмотря на сложности.\n"
        "Выберите опцию:",
        reply_markup=main_menu()
    )

@bot.message_handler(commands=['consult'])
def consult(message):
    print(f"[BOT] received /consult from {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "Расскажите о вашем запросе: какой документ, откуда и куда? "
        "(Например: 'Доверенность из Киева в Москву')"
    )
    bot.register_next_step_handler(message, save_lead)

def save_lead(message):
    username = message.from_user.username if getattr(message, "from_user", None) and message.from_user.username else "Unknown"
    try:
        with open('leads.txt', 'a', encoding='utf-8') as f:
            f.write(f"User: {username}, Query: {message.text}\n")
    except Exception as e:
        print(f"[leads.txt] write error: {e}")
    bot.send_message(
        message.chat.id,
        "Спасибо! Мы свяжемся с вами скоро. Пока посмотрите новости: "
        "https://www.is-logix.com/section/novosti/"
    )
    bot.send_message(message.chat.id, "Вернуться в меню?", reply_markup=main_menu())

@bot.message_handler(commands=['ua_ru'])
def ua_ru(message):
    print(f"[BOT] received /ua_ru from {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "Доставка из Украины в Россию: Несмотря на ситуацию, помогаем с доставкой различных документов. "
        "Есть некоторые ограничения. Гайд: https://www.is-logix.com/section/novosti/.\n"
        "Нужна помощь? /consult"
    )

@bot.message_handler(commands=['eu_ua'])
def eu_ua(message):
    print(f"[BOT] received /eu_ua from {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "Доставка документов из Европы в Украину: Визы, сертификаты, безопасно. Подробности: "
        "https://www.is-logix.com/section/novosti/.\nКонсультация: /consult"
    )

@bot.message_handler(commands=['news'])
def news(message):
    print(f"[BOT] received /news from {message.chat.id}")
    bot.send_message(
        message.chat.id,
        "Последние новости по логистике: Изменения в санкциях 2025 "
        "(https://www.is-logix.com/section/novosti/). "
        "Подписывайтесь на канал: https://t.me/doki_iz_UA_v_RU_BY"
    )

@bot.message_handler(func=lambda m: True)
def fallback(message):
    print(f"[BOT] fallback from {message.chat.id}: {message.text}")
    bot.send_message(message.chat.id, "Не понял. Выберите команду из меню.", reply_markup=main_menu())

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



