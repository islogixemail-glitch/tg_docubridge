# -*- coding: utf-8 -*-
"""DocuBridge admin commands: /orders, /order, /status + status inline buttons.

Only chat id == ADMIN_CHAT_ID may use them. Must be imported before the
catch-all text handler so these commands are matched first.
"""
from typing import Optional, Dict

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow_steps import format_track_message, _fmt_dt
from docubridge_sheets import update_status_async


def is_admin(chat_id) -> bool:
    try:
        return bool(ADMIN_CHAT_ID) and int(chat_id) == int(ADMIN_CHAT_ID)
    except (TypeError, ValueError):
        return False


def _p(row: Dict) -> Dict:
    return row.get("payload") or {}


def format_orders_list(rows) -> str:
    if not rows:
        return "Заявок пока нет."
    lines = [f"Последние заявки ({len(rows)}):", ""]
    for r in rows:
        p = _p(r)
        st = normalize_status(r.get("status")) or r.get("status")
        lines.append(
            f"{r.get('ticket')} | {_fmt_dt(r.get('created_at'))} | {route_line(p)} | "
            f"{p.get('name', '—')} | {p.get('phone', '—')} | {TRACK_STATUS_RU.get(st, st)}"
        )
    return "\n".join(lines)


def format_order_card(row: Dict, events) -> str:
    p = _p(row)
    q = compute_quote(p)
    price = f"€{q['price_eur']}" if q.get("price_eur") is not None else "по согласованию"
    lines = [
        f"Заявка {row.get('ticket')}",
        f"Chat ID: {row.get('chat_id')}",
        f"Создана: {_fmt_dt(row.get('created_at'))}",
        f"Обновлена: {_fmt_dt(row.get('updated_at'))}",
        "",
        f"Роль: {role_label(p.get('role') or 'sender')}",
        f"Маршрут: {route_line(p)}",
        f"Тип документа: {p.get('doc_type', '—')}",
        f"Объём: листов {p.get('pages_a4', '—')}, вес ≈ {p.get('weight_grams', 0)} г",
        f"Срочность: {p.get('urgency', '—')}",
        f"Оценка: {price}",
        f"Имя: {p.get('name', '—')}",
        f"Телефон: {p.get('phone', '—')}",
        f"Источник: {p.get('ref') or p.get('start_payload') or '—'}",
        "",
        format_track_message(row, events),
    ]
    return "\n".join(lines)


def client_status_message(ticket: str, status: str, comment: Optional[str]) -> str:
    msg = (
        f"📦 Отправление {ticket}: новый статус — «{TRACK_STATUS_RU.get(status, status)}»."
    )
    if comment:
        msg += f"\nКомментарий / трек-номер перевозчика: {comment}"
    msg += "\nПодробнее: /track"
    return msg


def apply_status_change(ticket: str, status_raw: str, comment: Optional[str] = None):
    """Returns (ok, message_for_admin)."""
    status = normalize_status(status_raw)
    if not status:
        return False, "Неизвестный статус. Допустимые: " + ", ".join(TRACK_STATUSES)
    ticket = (ticket or "").strip().upper()
    if not is_valid_ticket_format(ticket):
        return False, "Неверный номер. Формат: DB1234"
    if not comment:
        current = get_shipment_by_ticket(ticket)
        if current and normalize_status(current.get("status")) == status:
            return True, f"ℹ️ {current['ticket']} уже в этом статусе: «{TRACK_STATUS_RU[status]}»."
    row = update_shipment_status(ticket, status, comment)
    if not row:
        return False, f"Не удалось обновить {ticket} (не найдено или ошибка БД)."
    status_ru = TRACK_STATUS_RU[status]
    client_note = ""
    try:
        bot.send_message(int(row["chat_id"]), client_status_message(row["ticket"], status, comment))
        client_note = "клиент уведомлён"
    except Exception as e:
        print(f"[ADMIN status] client notify error: {e}")
        client_note = "клиента уведомить не удалось"
    try:
        update_status_async(row["ticket"], status_ru, comment)
    except Exception as e:
        print(f"[Sheets] status hook error: {e}")
    return True, f"✅ {row['ticket']}: «{status_ru}» ({client_note})."


@bot.message_handler(commands=["orders"], func=lambda m: is_admin(m.chat.id))
def admin_orders(message):
    bot.send_message(message.chat.id, format_orders_list(list_recent_shipments(20)))


@bot.message_handler(commands=["order"], func=lambda m: is_admin(m.chat.id))
def admin_order(message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Использование: /order DB1234")
        return
    ticket = parts[1].strip().upper()
    row = get_shipment_by_ticket(ticket)
    if not row:
        bot.send_message(message.chat.id, f"Заявка {ticket} не найдена.")
        return
    bot.send_message(
        message.chat.id,
        format_order_card(row, get_shipment_events(row["ticket"])),
        reply_markup=status_inline_keyboard(row["ticket"]),
    )


STATUS_USAGE = (
    "Использование: /status DB1234 <статус> [комментарий / трек-номер]\n"
    "Статусы: " + ", ".join(f"{k} ({v})" for k, v in TRACK_STATUS_RU.items())
)


@bot.message_handler(commands=["status"], func=lambda m: is_admin(m.chat.id))
def admin_status(message):
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        bot.send_message(message.chat.id, STATUS_USAGE)
        return
    comment = parts[3].strip() if len(parts) > 3 else None
    ok, msg = apply_status_change(parts[1], parts[2], comment)
    bot.send_message(message.chat.id, msg if ok else msg + "\n\n" + STATUS_USAGE)


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith(STATUS_CB_PREFIX + ":"))
def admin_status_button(call):
    if not is_admin(getattr(call.from_user, "id", None)):
        bot.answer_callback_query(call.id, "Нет доступа")
        return
    try:
        _, ticket, status = call.data.split(":", 2)
    except ValueError:
        bot.answer_callback_query(call.id, "Некорректные данные")
        return
    ok, msg = apply_status_change(ticket, status)
    bot.answer_callback_query(call.id, msg[:190])
    if call.message:
        bot.send_message(call.message.chat.id, msg)
