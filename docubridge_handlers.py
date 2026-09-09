# -*- coding: utf-8 -*-
"""DocuBridge Telegram handlers / webhook — Phase 2."""
import json
import traceback
from telebot.types import Update
from flask import request

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow import *  # noqa: F401,F403

# ------------ Handlers ------------

@bot.message_handler(commands=["start"])
def start(message):
    parts = (message.text or "").split(maxsplit=1)
    ref = parts[1].strip() if len(parts) > 1 else None
    action = resolve_start_payload(ref)

    if action.get("kind") == "route":
        start_send_flow(
            message.chat.id,
            role=action.get("role") or "sender",
            ref=ref,
            from_country=action.get("from_country"),
            to_country=action.get("to_country"),
        )
        return

    data = {}
    if ref:
        data["ref"] = ref
        data["start_payload"] = ref
    set_state(message.chat.id, "greeting", data)

    if action.get("kind") == "menu" and ref:
        msg = (
            "Вы пришли с витрины DocuBridge.\n"
            "Выберите: отправить документы, получить документы или другой пункт меню."
        )
    else:
        msg = (
            "Добро пожаловать в DocuBridge!\n\n"
            "Международная доставка документов по маршрутам:\n"
            "• Украина → Россия\n"
            "• Украина → Беларусь\n"
            "• Россия → Украина\n"
            "• Беларусь → Украина\n\n"
            "Сначала покажем ориентировочную цену и срок — контакты только если решите оформить заявку.\n"
            "Выберите действие в меню:"
        )
    save_message(message.chat.id, "/start", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())


@bot.message_handler(commands=["track"])
def track_cmd(message):
    start_track_flow(message.chat.id)


@bot.message_handler(commands=["reset", "menu"])
def reset(message):
    go_menu(message.chat.id, "Сессия сброшена. Выберите действие:")


@bot.message_handler(commands=["ai"])
def ai_ping(message):
    reply = ai_reply("Ответь одним словом: OK")
    save_message(message.chat.id, "/ai", reply)
    bot.send_message(message.chat.id, f"AI: {reply}")


@bot.message_handler(func=lambda m: True)
def any_text(message):
    handle_answer(message.chat.id, message.text or "")


# ------------ Webhook ------------

@app.route("/", methods=["GET"])
def index():
    return "OK", 200


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    try:
        if request.headers.get("content-type") == "application/json":
            json_data = json.loads(request.get_data().decode("utf-8"))
            update = Update.de_json(json_data)
            update_id = update.update_id
            print(f"[Webhook] Received update_id: {update_id}")

            if is_update_processed(update_id):
                print(f"[Webhook] Update {update_id} уже обработан, пропускаем")
                return "OK", 200

            mark_update_processed(update_id)
            print(f"[Webhook] Processing update_id: {update_id}")
            bot.process_new_updates([update])
            print(f"[Webhook] Update {update_id} processed successfully")
        else:
            print("[Webhook] Unsupported content-type")
    except Exception as e:
        print("[Webhook] error:", e)
        traceback.print_exc()
    return "OK", 200


def ensure_webhook():
    try:
        if not WEBHOOK_BASE:
            print("❌ ERROR: WEBHOOK_BASE не задан — бот не будет работать!")
            raise SystemExit(1)
        url = f"{WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}"
        bot.remove_webhook()
        ok = bot.set_webhook(url=url, drop_pending_updates=True)
        if ok:
            print(f"✅ Webhook set to: {url}")
        else:
            print("❌ ERROR: set_webhook returned False")
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ [Webhook] set error: {e}")
        raise SystemExit(1)


# ------------ Entrypoint ------------
if not SKIP_STARTUP:
    init_db_pool()
    ensure_tables()
    ensure_webhook()

if __name__ == "__main__":
    if SKIP_STARTUP:
        init_db_pool()
        ensure_tables()
    app.run(host="0.0.0.0", port=PORT, debug=False)
