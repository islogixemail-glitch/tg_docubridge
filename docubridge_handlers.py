# -*- coding: utf-8 -*-
"""DocuBridge Telegram handlers / webhook — Phase 2."""
import hmac
import json
import os
import statistics
import threading
import time
import traceback
from typing import Optional
from telebot.types import Update
from flask import request, jsonify
from urllib.parse import urlsplit

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow import *  # noqa: F401,F403
from docubridge_funnel import prompt_step
from docubridge_reminders import start_reminder_thread, reminder_health
from docubridge_sheets import start_sheets_thread, sheets_health
import docubridge_admin as _admin  # noqa: F401  (registers admin handlers before catch-all)
from docubridge_admin import *  # noqa: F401,F403
import docubridge_broadcast as _broadcast  # noqa: F401  (/broadcast_stuck + its buttons, before catch-all)
import docubridge_notify as _notify  # noqa: F401  (/chatid, /digest, my_chat_member, before catch-all)
import docubridge_handoff as _handoff  # noqa: F401  (attachments, «Что дальше» buttons)
from docubridge_notify import start_notify_thread, notify_health, update_allowed
from docubridge_db_notify import ensure_notify_tables

# ------------ Handlers ------------

@bot.message_handler(commands=["start"])
def start(message):
    parts = (message.text or "").split(maxsplit=1)
    ref = parts[1].strip() if len(parts) > 1 else None
    action = resolve_start_payload(ref)
    username = getattr(getattr(message, "from_user", None), "username", None)
    print(
        f"[Start] chat_id={message.chat.id} payload={ref or '-'} "
        f"username={('@' + username) if username else '-'} kind={action.get('kind')}"
    )
    record_start_event(message.chat.id, ref, username)

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
    send_hub(message.chat.id, from_showcase=bool(ref and action.get("kind") == "menu"))


def send_hub(chat_id: int, from_showcase: bool = False):
    head = "Вы пришли с витрины DocuBridge.\n\n" if from_showcase else ""
    msg = (
        head
        + "Добро пожаловать в DocuBridge! Доставляем бумажные документы "
        "между Украиной, Россией и Беларусью.\n\n"
        "Сначала покажем ориентировочную цену и срок — контакты только если решите оформить заявку.\n"
        "Выберите маршрут:"
    )
    save_message(chat_id, "/start", msg)
    bot.send_message(chat_id, msg, reply_markup=hub_inline_keyboard())
    bot.send_message(
        chat_id,
        "Или выберите действие в меню ниже. Справка — /help.",
        reply_markup=main_menu(),
    )


def send_fallback(chat_id: int):
    save_message(chat_id, None, FALLBACK_TEXT)
    bot.send_message(chat_id, FALLBACK_TEXT, reply_markup=main_menu())


@bot.message_handler(commands=["help"])
def help_cmd(message):
    save_message(message.chat.id, "/help", HELP_TEXT)
    bot.send_message(message.chat.id, HELP_TEXT, reply_markup=main_menu())


@bot.callback_query_handler(func=lambda c: parse_hub_callback(c.data) is not None)
def hub_route_cb(call):
    fc, tc = parse_hub_callback(call.data)
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"[hub] answer_callback_query failed: {e}")
    chat_id = call.message.chat.id
    _, data = get_state(chat_id)
    start_send_flow(chat_id, role="sender", ref=(data or {}).get("ref"),
                    from_country=fc, to_country=tc)


@bot.callback_query_handler(func=lambda c: (c.data or "") in OFFER_CALLBACKS)
def offer_cb(call):
    """Inline buttons under the route offer: calc, allowed docs, menu."""
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"[offer] answer_callback_query failed: {e}")
    chat = getattr(getattr(call, "message", None), "chat", None)
    if getattr(chat, "type", "private") != "private":
        return
    chat_id = chat.id
    state, data = get_state(chat_id)
    data = dict(data or {})
    if state == DB_ERROR_STATE:
        send_db_error(chat_id)
        return
    action = call.data or ""
    if action == OFFER_CB_MENU:
        go_menu(chat_id)
        return
    if action == OFFER_CB_ALLOWED:
        if state == "route_offer":
            show_allowed_docs(chat_id, offer=True)
        else:
            show_allowed_docs(chat_id)
        return
    # OFFER_CB_CALC — same path as the leftover «Рассчитать точно» text button
    if state == "route_offer":
        prompt_step(chat_id, "ask_doc_type", data)
        return
    if state in {None, "greeting", "choose_route"}:
        fc, tc = data.get("from_country"), data.get("to_country")
        if fc and tc and is_allowed_route(fc, tc):
            prompt_step(chat_id, "ask_doc_type", data)
            return
        start_send_flow(chat_id, role=data.get("role") or "sender", ref=data.get("ref"))


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


def _private(message) -> bool:
    """Client funnel only in private chats (groups: lead cards / status buttons only)."""
    return getattr(getattr(message, "chat", None), "type", "private") == "private"


@bot.message_handler(content_types=["contact"])
def contact_shared(message):
    """Telegram request_contact button on the phone step."""
    if not _private(message):
        return
    contact = getattr(message, "contact", None)
    phone = getattr(contact, "phone_number", "") or ""
    state, _ = get_state(message.chat.id)
    print(f"[Funnel] chat={message.chat.id} contact shared in state={state} phone={mask_phone(phone)}")
    if state == DB_ERROR_STATE:
        send_db_error(message.chat.id)
        return
    if state != "ask_phone" or not phone:
        send_fallback(message.chat.id)
        return
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")
    handle_answer(message.chat.id, phone, tg_name=profile_name(getattr(message, "from_user", None)))


@bot.message_handler(func=lambda m: True)
def any_text(message):
    if not _private(message):
        return
    if is_unknown_command(message.text):
        send_fallback(message.chat.id)
        return
    handle_answer(message.chat.id, message.text or "",
                  tg_name=profile_name(getattr(message, "from_user", None)),
                  user=getattr(message, "from_user", None))


# ------------ Webhook ------------

@app.route("/", methods=["GET"])
def index():
    return "OK", 200


def _update_chat_id(update):
    """chat id of a message / callback update, or None."""
    for attr in ("message", "edited_message"):
        m = getattr(update, attr, None)
        if m is not None and getattr(m, "chat", None) is not None:
            return m.chat.id
    cq = getattr(update, "callback_query", None)
    if cq is not None and getattr(cq, "message", None) is not None:
        return cq.message.chat.id
    return None


def _handle_update_json(json_data) -> None:
    """Dedup (+ state prefetch in the same query) → process → mark processed.
    The mark is queued for the background writer; it is queued BEFORE the 200
    is returned, so a redelivery (only on non-2xx) can never miss it."""
    update = Update.de_json(json_data)
    update_id = update.update_id
    print(f"[Webhook] Received update_id: {update_id}")
    chat_id = _update_chat_id(update)
    pre = check_update_and_state(update_id, chat_id) if chat_id is not None else None
    processed = pre[0] if pre is not None else is_update_processed(update_id)
    if processed:
        print(f"[Webhook] Update {update_id} уже обработан, пропускаем")
        return
    if not update_allowed(update):
        print(f"[Webhook] Update {update_id} ignored (group/channel, not a status button or admin command)")
        mark_update_processed(update_id)
        return
    print(f"[Webhook] Processing update_id: {update_id}")
    try:
        if pre is not None:
            prefetch_state(chat_id, *pre[1])
        try:
            bot.process_new_updates([update])
        except Exception as e:
            # Not marked as processed: a redelivery of this update will be handled again.
            print(f"[Webhook] Update {update_id} FAILED (not marked processed): {e}")
            traceback.print_exc()
            return
    finally:
        clear_prefetch()
    mark_update_processed(update_id)
    print(f"[Webhook] Update {update_id} processed successfully")


def _webhook_response():
    try:
        if request.headers.get("content-type") == "application/json":
            _handle_update_json(json.loads(request.get_data().decode("utf-8")))
        else:
            print("[Webhook] Unsupported content-type")
    except Exception as e:
        print("[Webhook] error:", e)
        traceback.print_exc()
    return "OK", 200


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    """Legacy route (secret in the path). Kept during the switch to /webhook."""
    return _webhook_response()


@app.route("/webhook", methods=["POST"])
def telegram_webhook_token():
    """New route: Telegram's X-Telegram-Bot-Api-Secret-Token header must match
    WEBHOOK_SECRET_TOKEN. Not configured → every request is rejected (fail closed)."""
    token = WEBHOOK_SECRET_TOKEN
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not token or not hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8")):
        print(f"[Webhook] /webhook rejected (403): token {'missing' if not got else 'mismatch'}"
              f"{'' if token else ', WEBHOOK_SECRET_TOKEN not configured'}")
        return "Forbidden", 403
    return _webhook_response()


def ensure_webhook() -> bool:
    """Idempotent setWebhook (same URL is simply re-set). Never raises: False on failure."""
    try:
        if not WEBHOOK_BASE:
            print("❌ ERROR: WEBHOOK_BASE не задан — бот не будет получать сообщения!")
            return False
        # Keep updates that queued up while the service was deploying/restarting
        # (e.g. /start from an ad): they are de-duplicated via processed_updates.
        if WEBHOOK_SECRET_TOKEN:
            ok = bot.set_webhook(url=f"{WEBHOOK_BASE}/webhook", secret_token=WEBHOOK_SECRET_TOKEN,
                                 drop_pending_updates=False)
            shown = f"{WEBHOOK_BASE}/webhook (secret_token header)"
        else:
            ok = bot.set_webhook(url=f"{WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}",
                                 drop_pending_updates=False)
            shown = f"{WEBHOOK_BASE}/webhook/<secret>"
        if ok:
            print(f"✅ Webhook set to: {shown}")
            return True
        print("❌ ERROR: set_webhook returned False")
    except Exception as e:
        print(f"❌ [Webhook] set error: {e}")
    return False


# ------------ Startup (lazy, per process, fork-safe) ------------
# Nothing is opened at import time: with gunicorn --preload the module is imported
# in the master before fork, so DB sockets / threads created there would be shared
# with (or missing in) the worker. Startup runs in the serving process instead:
# from gunicorn's post_worker_init hook and, as a fallback, on the first request.

STARTUP_RETRY_S = 30
_startup_lock = threading.Lock()
_startup = {"pid": None, "tables": False, "webhook": False, "last_try": 0.0}


def db_host() -> str:
    """Host name of DATABASE_URL only — never user, password or query."""
    if not DB_URL:
        return "-"
    try:
        if "://" in DB_URL:
            return urlsplit(DB_URL).hostname or "?"
        for part in DB_URL.split():
            if part.startswith("host="):
                return part.split("=", 1)[1].strip("'\"")
    except Exception:
        pass
    return "?"


def db_diagnostics(n: int = 5) -> Optional[float]:
    """Log the DB host and the median of n× SELECT 1 (one round trip each) in ms."""
    if not DB_URL:
        return None
    conn = get_conn()
    if not conn:
        print(f"[DB] host={db_host()} — no connection for latency check")
        return None
    times = []
    try:
        cur = conn.cursor()
        for _ in range(n):
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            times.append((time.perf_counter() - t0) * 1000)
        cur.close()
    except Exception as e:
        print(f"[DB] latency check failed: {e}")
        discard_conn(conn)
        return None
    return_conn(conn)
    med = statistics.median(times)
    print(f"[DB] host={db_host()} SELECT 1 ×{n}: median {med:.0f} ms "
          f"(min {min(times):.0f}, max {max(times):.0f})")
    return med


def startup_status() -> dict:
    same = _startup["pid"] == os.getpid()
    return {"tables": same and _startup["tables"], "webhook": same and _startup["webhook"]}


def ensure_started(force: bool = False) -> dict:
    """Idempotent: tables + webhook + reminder thread, once per process.
    Failed parts are retried on a later call (at most every STARTUP_RETRY_S)."""
    pid = os.getpid()
    if (_startup["pid"] == pid and _startup["tables"] and _startup["webhook"]
            and reminder_health() != "dead"
            and (not DB_URL or (_startup.get("notify") and notify_health() != "dead"))):
        return startup_status()
    if not _startup_lock.acquire(timeout=30 if force else 0):
        return startup_status()  # another thread is starting right now
    try:
        if _startup["pid"] != pid:
            _startup.update(pid=pid, tables=False, webhook=False, notify=False, last_try=0.0)
        now = time.time()
        if not force and now - _startup["last_try"] < STARTUP_RETRY_S:
            return startup_status()
        _startup["last_try"] = now
        print(f"[Startup] pid {pid}: tables={_startup['tables']} webhook={_startup['webhook']}")
        if not _startup["tables"]:
            try:
                _startup["tables"] = bool(ensure_tables())
            except Exception as e:
                print(f"[Startup] ensure_tables error: {e}")
            if _startup["tables"]:
                try:
                    db_diagnostics()
                except Exception as e:
                    print(f"[Startup] db diagnostics error: {e}")
        if not _startup["webhook"]:
            _startup["webhook"] = ensure_webhook()
        try:
            start_reminder_thread()
        except Exception as e:
            print(f"[Startup] reminder thread error: {e}")
        try:
            start_sheets_thread()   # Google Sheet check + outbox; off without env
        except Exception as e:
            print(f"[Startup] sheets thread error: {e}")
        if _startup["tables"] and not _startup.get("notify"):
            try:
                _startup["notify"] = ensure_notify_tables()
            except Exception as e:
                print(f"[Startup] notify tables error: {e}")
        try:
            if _startup.get("notify"):
                start_notify_thread()   # lead notify outbox + daily digest
        except Exception as e:
            print(f"[Startup] notify thread error: {e}")
        return startup_status()
    finally:
        _startup_lock.release()


@app.before_request
def _lazy_startup():
    if SKIP_STARTUP:
        return None
    try:
        ensure_started()
    except Exception as e:  # never block a request because of startup
        print(f"[Startup] error: {e}")
    return None


def db_ping() -> bool:
    if not DB_URL:
        return False

    def _do(conn):
        cur = conn.cursor()
        cur.execute("SELECT 1")
        ok = cur.fetchone() is not None
        cur.close()
        return ok

    return bool(db_run("health", _do, default=False))


@app.route("/health", methods=["GET"])
def health():
    db_ok = db_ping()
    rem = reminder_health()
    st = startup_status()
    ok = db_ok and rem != "dead" and st["webhook"]
    body = {"ok": ok, "db": db_ok, "reminders": rem, "webhook": st["webhook"],
            "tables": st["tables"], "sheets": sheets_health(), "notify": notify_health(),
            "pid": os.getpid()}
    return jsonify(body), (200 if ok else 503)


# ------------ Entrypoint ------------
if __name__ == "__main__":
    if not SKIP_STARTUP:
        ensure_started(force=True)
    app.run(host="0.0.0.0", port=PORT, debug=False)
