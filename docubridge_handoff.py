# -*- coding: utf-8 -*-
"""Hand a client over to a human only in non-standard cases.

Triggers (checked BEFORE the AI):
  * the client asks for a person (also the «Связаться с менеджером» / «Нужен человек» buttons);
  * a discount request; a complaint / dispute;
  * a non-standard attachment (photo, file, voice, video …);
  * an order the bot cannot finish alone: weight > 0.5 kg (price discussed with the manager);
  * a question the AI could not answer.
Everything else is answered by the bot itself (FAQ, price from helpers, /track).

The card goes to the same Telegram target as the lead cards (LEADS_CHAT_ID, else
ADMIN_CHAT_ID). At most one card per chat per HANDOFF_WINDOW_MIN (handoff_log in the DB;
in-memory fallback when the DB is unavailable). Never raises into the request.
"""
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import main_menu
import docubridge_notify_cfg as ncfg
import docubridge_db_notify as ndb
import docubridge_next_steps as ns
import docubridge_lang as lang_mod
from docubridge_texts import t
from docubridge_funnel import prompt_step, FUNNEL_STATES, STEP_TITLES_RU

HANDOFF_WINDOW_MIN = 30
CONFIG_WINDOW_MIN = 60 * 24 * 365 * 10   # «не настроено» alert: once per order
TEXT_MAX = 500

REASON_RU = {
    "human": "клиент просит человека",
    "discount": "просьба о скидке",
    "complaint": "жалоба / спор",
    "attachment": "клиент прислал вложение",
    "overweight": "вес больше 0,5 кг — нужен расчёт",
    "question": "вопрос, на который бот не ответил",
}

_HUMAN_RE = re.compile(
    r"\bменеджер|\bоператор|живой человек|живого человека|с человеком|позовите|"
    r"\bпозвоните|\bперезвоните|\bсвяжитесь со мной|нужен человек|"
    r"жива людина|живу людину|з людиною|покличте|\bзателефонуйте|\bпередзвоніть|потрібна людина"
)
_DISCOUNT_RE = re.compile(r"\bскидк|\bдешевл|\bподешевле|\bуступит|\bторг(?:уетесь|уемся|анутьс)|\bпромокод"
                          r"|\bзнижк|\bдешевш")
_COMPLAINT_RE = re.compile(
    r"\bжалоб|\bпожаловат|\bпретензи|\bвозврат|\bверните|\bобман|\bмошенн|\bпотеряли|"
    r"не доставил|не пришл|не получил|\bразвод|"
    r"\bскарг|\bповерніть|\bповернення|\bшахра|\bзагубили|не доставили|не надійш|не отримав"
)

ATTACHMENT_TYPES = ["photo", "document", "video", "voice", "audio", "video_note", "animation"]
ATTACHMENT_RU = {
    "photo": "фото", "document": "файл", "video": "видео", "voice": "голосовое",
    "audio": "аудио", "video_note": "видеосообщение", "animation": "GIF",
}

_STEP_RU = dict(STEP_TITLES_RU, greeting="меню", choose_route="выбор маршрута",
                completed="заявка оформлена", edit_choose="исправление заявки",
                await_track="ввод номера для отслеживания")

_mem_lock = threading.Lock()
_mem_last: Dict[tuple, float] = {}       # (chat, scope) → ts, used only when the DB is down


def detect_handoff(text: Optional[str]) -> Optional[str]:
    """'complaint' | 'discount' | 'human' | None for a free-text message."""
    low = (text or "").strip().lower()
    if not low or low.startswith("/"):
        return None
    if _COMPLAINT_RE.search(low):
        return "complaint"
    if _DISCOUNT_RE.search(low):
        return "discount"
    if _HUMAN_RE.search(low):
        return "human"
    return None


def client_reply(reason: str, ticket: Optional[str] = None, lang: str = "ru") -> str:
    tail = t("ho_tail", lang, ticket=ticket) if ticket else ""
    kw = dict(manager=MANAGER_CONTACT, rt=ns.rt(lang), tail=tail)
    if reason == "discount":
        return t("ho_discount", lang, price=PRICE_ORDINARY_EUR, max_g=ENVELOPE_MAX_G, **kw)
    if reason == "attachment":
        return t("ho_attachment", lang, **kw)
    return t("ho_human", lang, **kw)


def _who(user) -> str:
    if user is None:
        return "—"
    un = getattr(user, "username", None)
    name = " ".join(p for p in (getattr(user, "first_name", None) or "",
                                getattr(user, "last_name", None) or "") if isinstance(p, str) and p).strip()
    parts = [f"@{un}" if un else "", name]
    return " ".join(p for p in parts if p) or "—"


def build_handoff_card(chat_id: int, reason: str, text: Optional[str], state: Optional[str],
                       data: Optional[Dict], user=None, ticket: Optional[str] = None,
                       extra: Optional[str] = None) -> str:
    data = data or {}
    ticket = ticket or data.get("ticket")
    head = f"🙋 Нужен человек: {REASON_RU.get(reason, reason)}"
    if ncfg.is_test_lead(chat_id):
        head = f"🧪 ТЕСТ — администратор\n{head}"
    lines = [
        head,
        f"Ticket: {ticket or '—'}",
        f"Chat ID: {chat_id}",
        f"Клиент: {_who(user) if user is not None else (data.get('name') or data.get('tg_name') or '—')}",
        f"Шаг: {_STEP_RU.get(state or '', state or '—')}",
    ]
    if data.get("from_country") and data.get("to_country"):
        lines.append(f"Маршрут: {data['from_country']} → {data['to_country']}")
    if text:
        t = text.strip()
        lines.append(f"Сообщение: «{t[:TEXT_MAX]}{'…' if len(t) > TEXT_MAX else ''}»")
    if extra:
        lines.append(extra)
    lines.append(f"Карточка заявки: /order {ticket}" if ticket else "Заявки нет.")
    return "\n".join(lines)


def _claim(chat_id: int, scope: str, reason: str, ticket: Optional[str], window_min: int) -> bool:
    ok = ndb.claim_handoff(chat_id, scope, reason, ticket, window_min)
    if ok is not None:
        return bool(ok)
    key = (int(chat_id), scope)
    now = time.time()
    with _mem_lock:
        last = _mem_last.get(key)
        if last is not None and now - last < window_min * 60:
            return False
        _mem_last[key] = now
    return True


def _target(chat_id: int) -> int:
    if ncfg.is_test_lead(chat_id) and not (ncfg.leads_chat_id() and ncfg.notify_test_leads()):
        return 0                       # admin testing with no group: the admin is the target anyway
    return ncfg.notify_target()


def request_handoff(chat_id: int, reason: str, text: Optional[str] = None, state: Optional[str] = None,
                    data: Optional[Dict] = None, user=None, ticket: Optional[str] = None,
                    forward_message_id: Optional[int] = None, extra: Optional[str] = None,
                    scope: str = "client", window_min: int = HANDOFF_WINDOW_MIN) -> bool:
    """Send the «нужен человек» card (throttled). True if a card was sent. Never raises."""
    try:
        target = _target(chat_id)
        if not target:
            print(f"[Handoff] chat={chat_id} reason={reason}: no LEADS_CHAT_ID/ADMIN_CHAT_ID — skipped")
            return False
        if not _claim(chat_id, scope, reason, ticket or (data or {}).get("ticket"), window_min):
            print(f"[Handoff] chat={chat_id} reason={reason}: throttled ({scope}, {window_min} min)")
            return False
        card = build_handoff_card(chat_id, reason, text, state, data, user, ticket, extra)
        bot.send_message(target, card)
        if forward_message_id:
            try:
                bot.forward_message(target, chat_id, forward_message_id)
            except Exception as e:
                print(f"[Handoff] forward failed: {e}")
        print(f"[Handoff] chat={chat_id} reason={reason} → card sent to {target}")
        return True
    except Exception as e:
        print(f"[Handoff] chat={chat_id} reason={reason} failed: {e}")
        return False


def send_group_note(chat_id: int, text: str, scope: str, window_min: int = CONFIG_WINDOW_MIN) -> bool:
    """A one-line note to the leads target (same target/test rules as the cards), once per scope."""
    try:
        target = _target(chat_id)
        if not target or not _claim(chat_id, scope, "note", None, window_min):
            return False
        if ncfg.is_test_lead(chat_id):
            text = f"🧪 ТЕСТ — администратор\n{text}"
        bot.send_message(target, text)
        print(f"[Handoff] chat={chat_id} note {scope} → {target}")
        return True
    except Exception as e:
        print(f"[Handoff] note {scope} failed: {e}")
        return False


# ------------ replying in the chat while keeping the funnel step ------------

def reply_keep_step(chat_id: int, text: str, state: Optional[str], data: Dict) -> None:
    if state in FUNNEL_STATES or state == "route_offer":
        prompt_step(chat_id, state, data, prefix=text)
        return
    save_message(chat_id, None, text)
    bot.send_message(chat_id, text, reply_markup=main_menu())


_BUTTON_TEXTS = None


def _is_button(s: str) -> bool:
    global _BUTTON_TEXTS
    if _BUTTON_TEXTS is None:
        _BUTTON_TEXTS = {MENU_SEND, MENU_RECEIVE, MENU_TRACK, MENU_ALLOWED, MENU_MANAGER, BTN_APPLY,
                         BTN_MENU, BTN_CONFIRM, BTN_CANCEL, BTN_CALC, BTN_BACK, BTN_EDIT, BTN_OTHER_CITY,
                         BTN_EDIT_DONE, BTN_SHARE_CONTACT, DOC_OTHER, *DOC_TYPE_BUTTONS,
                         *VOLUME_PRESETS.keys(), *ROUTE_LABELS, *EDIT_FIELDS.keys(),
                         *[c for cs in POPULAR_CITIES.values() for c in cs]}
    return s in _BUTTON_TEXTS or s.startswith(NAME_BTN_PREFIX)


def is_button(s: str) -> bool:
    return _is_button((s or "").strip())


def handle_special_text(chat_id: int, text: str, state: Optional[str], data: Dict, user=None) -> bool:
    """Handoff triggers and payment/handover FAQ, before the funnel and the AI.
    True if the message was answered here."""
    s = (text or "").strip()
    if not s or s.startswith("/"):
        return False
    if s == MENU_MANAGER:
        # The button keeps its old behaviour (contact shown, step kept) + the operator is told.
        request_handoff(chat_id, "human", s, state, data, user)
        return False
    if _is_button(s) or state == "await_track":
        return False
    lang = lang_mod.get_lang(chat_id, user)
    reason = detect_handoff(s)
    if reason:
        ticket = data.get("ticket")
        request_handoff(chat_id, reason, s, state, data, user, ticket)
        reply_keep_step(chat_id, client_reply(reason, ticket, lang), state, data)
        return True
    intent = ns.faq_intent(s)
    if intent:
        ticket = data.get("ticket") if state == "completed" else None
        if intent == "address" and ticket:
            msg = ns.address_answer(ticket, lang)
            save_message(chat_id, None, msg)
            bot.send_message(chat_id, msg, reply_markup=ns.terms_keyboard(ticket, lang))
            return True
        if intent == "payment" and ticket and not data.get("pay_currency"):
            ask_currency(chat_id, ticket, data, lang=lang)
            return True
        if intent == "address":
            ans = ns.address_answer(None, lang)
        elif intent == "payment":
            requested = bool(ticket) and ndb.pay_requested(ticket, chat_id) is True
            ans = ns.payment_answer(ticket, data.get("pay_currency"), data, lang, requested)
        else:
            ans = ns.handover_answer(ticket, data, lang)
        reply_keep_step(chat_id, ans, state, data)
        return True
    return False


# ------------ attachments ------------

@bot.message_handler(content_types=ATTACHMENT_TYPES)
def attachment_received(message):
    chat = getattr(message, "chat", None)
    if getattr(chat, "type", "private") != "private":
        return
    chat_id = chat.id
    kind = getattr(message, "content_type", None) or "file"
    state, data = get_state(chat_id)
    data = dict(data or {}) if state != DB_ERROR_STATE else {}
    print(f"[Handoff] chat={chat_id} attachment {kind} in state={state}")
    caption = getattr(message, "caption", None)
    request_handoff(chat_id, "attachment", caption, state, data, getattr(message, "from_user", None),
                    data.get("ticket"), forward_message_id=getattr(message, "message_id", None),
                    extra=f"Тип вложения: {ATTACHMENT_RU.get(kind, kind)} (переслано ниже)")
    save_message(chat_id, f"[{kind}]", None)
    lang = lang_mod.get_lang(chat_id, getattr(message, "from_user", None))
    reply_keep_step(chat_id, client_reply("attachment", data.get("ticket"), lang),
                    state if state != DB_ERROR_STATE else None, data)


# ------------ payment currency ------------

def _order_data(chat_id: int, ticket: str, payload=None) -> Dict:
    """Route data for texts: the order payload (DB) or the completed state of this order."""
    if isinstance(payload, dict) and payload:
        return payload
    state, data = get_state(chat_id)
    data = dict(data or {})
    return data if state == "completed" and data.get("ticket") == ticket else {}


def ask_currency(chat_id: int, ticket: str, data: Optional[Dict] = None, with_actions: bool = False,
                 lang: str = "ru") -> None:
    msg = ns.currency_question(ticket, data, lang)
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=ns.currency_keyboard(ticket, with_actions=with_actions, lang=lang))


def _cb_chat(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"[Callback] answer_callback_query failed: {e}")
    chat = getattr(getattr(call, "message", None), "chat", None)
    if chat is None or getattr(chat, "type", "private") != "private":
        return None
    return chat.id


@bot.callback_query_handler(func=lambda c: ns.parse_pc_callback(c.data) is not None)
def currency_cb(call):
    """Currency chosen. Never changes the funnel step; stale / foreign buttons are harmless."""
    chat_id = _cb_chat(call)
    if chat_id is None:
        return
    lang = lang_mod.get_lang(chat_id, getattr(call, "from_user", None))
    ticket, cur = ns.parse_pc_callback(call.data)
    if cur not in ns.pcfg.currencies():
        print(f"[Pay] chat={chat_id} stale currency button {cur} for {ticket}")
        msg = t("pay_stale", lang)
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=ns.currency_keyboard(ticket, with_actions=False, lang=lang))
        return
    stored = ndb.set_pay_currency(ticket, chat_id, cur)
    if stored is False:
        print(f"[Pay] chat={chat_id} currency button for unknown/foreign order {ticket}")
        msg = t("order_not_found", lang, ticket=ticket)
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=main_menu())
        return
    data = _order_data(chat_id, ticket, stored)
    state, sdata = get_state(chat_id)
    if state == "completed" and (sdata or {}).get("ticket") == ticket:
        set_state(chat_id, "completed", dict(sdata, pay_currency=cur))
    print(f"[Pay] chat={chat_id} {ticket} currency={cur} stored={stored is not None}")
    # Details go out ONLY after the documents arrived (admin /payreq or «📥 Документы получены»).
    requested = stored is not None and ndb.pay_requested(ticket, chat_id) is True
    msg = (ns.payment_request_text(ticket, data, cur, lang) if requested
           else ns.currency_saved_text(ticket, cur, data, lang))
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=ns.after_payment_keyboard(ticket, lang))
    send_group_note(chat_id, ns.group_currency_note(ticket, cur, stored is not None, requested),
                    scope=f"cur:{ticket}:{cur}:{int(requested)}")


# ------------ «Условия подходят, нужен адрес» ------------

@bot.callback_query_handler(func=lambda c: ns.parse_tc_callback(c.data) is not None)
def terms_cb(call):
    """Consent stored once (terms_accepted_at); the 📬 card goes to the group once per order
    (not limited by the 30-min «нужен человек» throttle)."""
    chat_id = _cb_chat(call)
    if chat_id is None:
        return
    user = getattr(call, "from_user", None)
    lang = lang_mod.get_lang(chat_id, user)
    ticket = ns.parse_tc_callback(call.data)
    res = ndb.accept_terms(ticket, chat_id)
    if res is False:
        msg = t("order_not_found", lang, ticket=ticket)
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=main_menu())
        return
    if res is None:   # DB down: still tell the manager, once per order in this process
        first = _claim(chat_id, f"addr:{ticket}", "address", ticket, CONFIG_WINDOW_MIN)
        at, payload = None, _order_data(chat_id, ticket)
    else:
        first, at, payload = res["first"], res["at"], _order_data(chat_id, ticket, res["payload"])
    print(f"[Terms] chat={chat_id} {ticket} accepted first={first} db={res is not None}")
    if first:
        target = _target(chat_id)
        if target:
            who = _who(user) if user is not None else (payload.get("name") or "—")
            when = fmt_local(at, label=True) if at else (fmt_local(datetime.now(timezone.utc), label=True)
                                                        + " (БД недоступна — согласие не сохранено)")
            card = ns.address_card(ticket, payload, who, chat_id, when, lang)
            if ncfg.is_test_lead(chat_id):
                card = f"🧪 ТЕСТ — администратор\n{card}"
            try:
                bot.send_message(target, card)
            except Exception as e:
                print(f"[Terms] address card failed: {e}")
        msg = t("terms_ok", lang, rt=ns.rt(lang))
    else:
        msg = t("terms_again", lang, ticket=ticket, rt=ns.rt(lang))
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())


# ------------ buttons under «Что дальше» ------------

@bot.callback_query_handler(func=lambda c: ns.parse_ns_callback(c.data) is not None)
def next_steps_cb(call):
    chat_id = _cb_chat(call)
    if chat_id is None:
        return
    lang = lang_mod.get_lang(chat_id, getattr(call, "from_user", None))
    action, ticket = ns.parse_ns_callback(call.data)
    if action == "pay":
        ask_currency(chat_id, ticket, _order_data(chat_id, ticket), lang=lang)
        return
    elif action == "hand":
        msg = ns.handover_answer(ticket, _order_data(chat_id, ticket), lang)
    elif action == "track":
        from docubridge_flow_steps import handle_track_ticket
        handle_track_ticket(chat_id, ticket)
        return
    else:
        state, data = get_state(chat_id)
        request_handoff(chat_id, "human", "кнопка «Нужен человек»", state, dict(data or {}),
                        getattr(call, "from_user", None), ticket)
        msg = client_reply("human", ticket, lang)
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())
