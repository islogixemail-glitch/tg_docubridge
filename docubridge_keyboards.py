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


def quote_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton(BTN_APPLY))
    kb.add(KeyboardButton(BTN_MENU))
    return kb


def confirm_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton(BTN_CONFIRM))
    kb.add(KeyboardButton(BTN_CANCEL))
    return kb


def role_label(role: str) -> str:
    return "получатель" if role == "receiver" else "отправитель"


def route_line(data: Dict) -> str:
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
        f"Срочность: {data.get('urgency', '—')}\n\n"
        f"{price_line}\n"
        f"{eta_line}"
        f"{notes}\n\n"
        "Контакты пока не нужны. Нажмите «Оформить заявку», чтобы продолжить, "
        "или «В меню»."
    )


def format_confirm_summary(data: Dict) -> str:
    q = compute_quote(data)
    price = f"€{q['price_eur']}" if q["price_eur"] is not None else "по согласованию"
    return (
        "Проверьте заявку:\n\n"
        f"Роль: {role_label(data.get('role') or 'sender')}\n"
        f"Маршрут: {route_line(data)}\n"
        f"Документ: {data.get('doc_type')}\n"
        f"Объём: {data.get('pages_a4') or '—'} л. / ≈ {data.get('weight_grams') or 0} г\n"
        f"Срочность: {data.get('urgency')}\n"
        f"Оценка: {price}; срок {q.get('eta_working') or '—'} раб. дней\n"
        f"Имя: {data.get('name')}\n"
        f"Телефон: {data.get('phone')}\n\n"
        "Нажмите «Подтвердить» или «Отмена»."
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
    return kb


def notify_admin_lead(source_chat_id: int, payload: Dict):
    """Manager card — plain text to avoid Markdown parse breaks."""
    if not ADMIN_CHAT_ID:
        print("[ADMIN] ADMIN_CHAT_ID не задан — уведомление не отправлено")
        return
    if ADMIN_CHAT_ID == source_chat_id:
        print("[ADMIN] ADMIN_CHAT_ID == user chat — skip (test mode)")
        return
    try:
        q = compute_quote(payload)
        price_line = (
            f"Оценка: €{q['price_eur']} (до {q['threshold_g']} г)"
            if q["price_eur"] is not None
            else "Оценка: по согласованию"
        )
        eta_line = (
            f"Срок: ориентировочно {q['eta_working']} рабочих дней"
            if q.get("eta_working")
            else "Срок: требует подтверждения"
        )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        ref = payload.get("ref") or payload.get("start_payload") or "—"
        lines = [
            "Новый лид DocuBridge",
            f"Ticket: {payload.get('ticket', '—')}",
            f"Chat ID: {source_chat_id}",
            f"Time: {ts}",
            f"Source/ref: {ref}",
            "",
            f"Роль: {role_label(payload.get('role') or 'sender')}",
            f"Маршрут: {route_line(payload)}",
            f"Тип документа: {payload.get('doc_type', '—')}",
            f"Объём: листов {payload.get('pages_a4', '—')}, вес ≈ {payload.get('weight_grams', 0)} г",
            f"Срочность: {payload.get('urgency', '—')}",
            price_line,
            eta_line,
            "",
            f"Имя: {payload.get('name', '—')}",
            f"Телефон: {payload.get('phone', '—')}",
        ]
        ticket = payload.get("ticket")
        markup = status_inline_keyboard(ticket) if ticket else None
        bot.send_message(ADMIN_CHAT_ID, "\n".join(lines), reply_markup=markup)
    except Exception as e:
        print(f"[ADMIN notify] error: {e}")


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
        bot.send_message(ADMIN_CHAT_ID, "\n".join(lines))
    except Exception as e:
        print(f"[ADMIN notify failed-order] error: {e}")
