# -*- coding: utf-8 -*-
"""DocuBridge keyboards and quote formatting — Phase 2."""
from datetime import datetime, timezone
from typing import Optional, Dict
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from docubridge_runtime import *  # noqa: F401,F403

def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton(MENU_SEND))
    kb.add(KeyboardButton(MENU_RECEIVE))
    kb.add(KeyboardButton(MENU_TRACK))
    kb.add(KeyboardButton(MENU_ALLOWED))
    kb.add(KeyboardButton(MENU_MANAGER))
    return kb


def hub_inline_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    for key, (fc, tc) in START_PAYLOAD_ROUTES.items():
        kb.add(InlineKeyboardButton(f"{fc} → {tc}", callback_data=hub_callback_data(key)))
    return kb


def route_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for label in ROUTE_LABELS:
        kb.add(KeyboardButton(label))
    kb.add(KeyboardButton(BTN_MENU))
    return kb


def urgency_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton("обычная"), KeyboardButton("срочная"))
    kb.add(KeyboardButton(BTN_MENU))
    return kb


def offer_inline_keyboard() -> InlineKeyboardMarkup:
    """Buttons under the route-offer message (not a bottom reply keyboard)."""
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(BTN_CALC, callback_data=OFFER_CB_CALC))
    kb.row(InlineKeyboardButton(MENU_ALLOWED, callback_data=OFFER_CB_ALLOWED))
    kb.row(InlineKeyboardButton(BTN_MENU, callback_data=OFFER_CB_MENU))
    return kb


def send_offer_message(chat_id: int, text: str) -> None:
    """Send offer (or an offer-step follow-up) with inline buttons.

    Also hides any leftover reply keyboard (old «Рассчитать точно», main menu,
    route list, doc-type keys after «← Назад»). Telegram does not allow
    ReplyKeyboardRemove and an inline keyboard on the same send, so we send
    Remove first and attach the inline buttons by editing that message.
    If edit is unavailable (no integer message_id — e.g. a test stub), we
    send one message with the inline keyboard only.
    Does not touch later reply keyboards (phone request_contact, cities, …):
    those are sent by their own steps after the user leaves the offer.
    """
    kb = offer_inline_keyboard()
    sent = bot.send_message(chat_id, text, reply_markup=ReplyKeyboardRemove())
    mid = getattr(sent, "message_id", None)
    if isinstance(mid, int):
        try:
            bot.edit_message_reply_markup(chat_id, mid, reply_markup=kb)
            return
        except Exception as e:
            print(f"[Offer] edit_message_reply_markup failed: {e}")
    bot.send_message(chat_id, text, reply_markup=kb)


def _nav_row(kb: ReplyKeyboardMarkup, back: bool = True) -> ReplyKeyboardMarkup:
    """«← Назад» + «Связаться с менеджером» on every funnel step."""
    if back:
        kb.row(KeyboardButton(BTN_BACK), KeyboardButton(MENU_MANAGER))
    else:
        kb.row(KeyboardButton(MENU_MANAGER))
    return kb


def doc_type_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    kb.add(*[KeyboardButton(t) for t in DOC_TYPE_BUTTONS])
    return _nav_row(kb)


def volume_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    kb.add(*[KeyboardButton(t) for t in VOLUME_PRESETS])
    return _nav_row(kb)


def city_keyboard(country: Optional[str]) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    cities = POPULAR_CITIES.get(normalize_country(country or "") or "", [])
    if cities:
        kb.add(*[KeyboardButton(c) for c in cities])
    kb.row(KeyboardButton(BTN_OTHER_CITY))
    return _nav_row(kb)


def name_keyboard(tg_name: Optional[str] = None) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if tg_name:
        kb.row(KeyboardButton(NAME_BTN_PREFIX + tg_name))
    return _nav_row(kb)


def phone_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton(BTN_SHARE_CONTACT, request_contact=True))
    return _nav_row(kb)


def quote_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton(BTN_APPLY))
    _nav_row(kb)
    kb.add(KeyboardButton(BTN_MENU))
    return kb


def confirm_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton(BTN_CONFIRM))
    kb.row(KeyboardButton(BTN_EDIT), KeyboardButton(BTN_CANCEL))
    kb.row(KeyboardButton(MENU_MANAGER))
    return kb


def edit_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    kb.add(*[KeyboardButton(t) for t in EDIT_FIELDS])
    kb.row(KeyboardButton(BTN_EDIT_DONE))
    return kb


def role_label(role: str) -> str:
    return "получатель" if role == "receiver" else "отправитель"


def route_line(data: Dict) -> str:
    if not data.get("from_city") and not data.get("to_city"):
        return f"{data.get('from_country')} → {data.get('to_country')}"
    return (
        f"{data.get('from_country')}/{data.get('from_city')} → "
        f"{data.get('to_country')}/{data.get('to_city')}"
    )


def format_quote_message(data: Dict) -> str:
    q = compute_quote(data)
    if q["price_eur"] is not None:
        price_line = f"Ориентировочная стоимость: €{q['price_eur']} (конверт до {ENVELOPE_MAX_G} г)"
    else:
        price_line = "Ориентировочная стоимость: по согласованию"

    if q.get("eta_working"):
        eta_line = f"Срок: ориентировочно {q['eta_working']} рабочих дней"
    else:
        eta_line = "Срок: требует подтверждения маршрута"

    vol_parts = []
    if data.get("pages_a4"):
        vol_parts.append(f"{data['pages_a4']} л. A4")
    if data.get("weight_grams"):
        vol_parts.append(f"≈ {data['weight_grams']} г")
    vol_line = ", ".join(vol_parts) if vol_parts else "—"

    notes = f"\nПримечание: {q['notes']}" if q.get("notes") else ""

    return (
        "📋 Предварительный расчёт\n\n"
        f"Роль: {role_label(data.get('role') or 'sender')}\n"
        f"Маршрут: {route_line(data)}\n"
        f"Тип документа: {data.get('doc_type', '—')}\n"
        f"Объём: {vol_line}\n"
        "\n"
        f"{price_line}\n"
        f"{eta_line}"
        f"{notes}\n\n"
        "Цена от городов не зависит. Контакты пока не нужны. "
        "Нажмите «Оформить заявку» — уточним города и контакт для карточки заказа, "
        "или «В меню»."
    )


def format_confirm_summary(data: Dict) -> str:
    q = compute_quote(data)
    price = f"€{q['price_eur']}" if q["price_eur"] is not None else "по согласованию"
    return (
        "Проверьте заявку (последний шаг):\n\n"
        f"Роль: {role_label(data.get('role') or 'sender')}\n"
        f"Маршрут: {route_line(data)}\n"
        f"Документ: {data.get('doc_type')}\n"
        f"Объём: {data.get('pages_a4') or '—'} л. / ≈ {data.get('weight_grams') or 0} г\n"
        f"Оценка: {price}; срок {q.get('eta_working') or '—'} раб. дней\n"
        f"Имя: {data.get('name')}\n"
        f"Телефон: {data.get('phone')}\n\n"
        "Всё верно? Нажмите «Подтвердить». "
        "Если нужно поменять город, имя, телефон или документ — «✏️ Исправить»."
    )


STATUS_CB_PREFIX = "st"


def status_inline_keyboard(ticket: str) -> InlineKeyboardMarkup:
    """Inline buttons for admin: callback_data = st:<ticket>:<status>."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        *[
            InlineKeyboardButton(
                TRACK_STATUS_RU[st].capitalize(),
                callback_data=f"{STATUS_CB_PREFIX}:{ticket}:{st}",
            )
            for st in TRACK_STATUSES
        ]
    )
    kb.row(InlineKeyboardButton("📥 Документы получены — запросить оплату",
                                callback_data=f"{STATUS_CB_PREFIX}:{ticket}:payreq"))
    return kb


def notify_admin_lead(source_chat_id: int, payload: Dict):
    """New-lead card → LEADS_CHAT_ID or ADMIN_CHAT_ID (+ e-mail), via the notify outbox.
    See docubridge_notify; never raises, never delays the client's reply in outbox mode."""
    from docubridge_notify import notify_new_lead
    return notify_new_lead(source_chat_id, payload, admin_override=ADMIN_CHAT_ID)


def notify_admin_order_failed(source_chat_id: int, payload: Dict):
    """Alert admin that a confirmed order could not be saved (no ticket issued)."""
    if not ADMIN_CHAT_ID:
        print("[ADMIN] ADMIN_CHAT_ID не задан — уведомление об ошибке не отправлено")
        return
    try:
        lines = [
            "⚠️ Ошибка сохранения заявки (номер клиенту НЕ выдан)",
            f"Chat ID: {source_chat_id}",
            f"Маршрут: {route_line(payload)}",
            f"Имя: {payload.get('name', '—')}",
            f"Телефон: {payload.get('phone', '—')}",
            "Проверьте БД и свяжитесь с клиентом.",
        ]
        import docubridge_notify_cfg as ncfg
        bot.send_message(ncfg.notify_target(ADMIN_CHAT_ID), "\n".join(lines))
    except Exception as e:
        print(f"[ADMIN notify failed-order] error: {e}")
