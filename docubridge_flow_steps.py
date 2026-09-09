# -*- coding: utf-8 -*-
"""DocuBridge dialog flow — Phase 2."""
from typing import Optional, Dict, Any
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403

# ------------ Flow steps ------------

def go_menu(chat_id: int, text: Optional[str] = None):
    msg = text or "Главное меню. Выберите действие:"
    set_state(chat_id, "greeting", {})
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())


def start_send_flow(
    chat_id: int,
    role: str = "sender",
    ref: Optional[str] = None,
    from_country: Optional[str] = None,
    to_country: Optional[str] = None,
):
    data: Dict[str, Any] = {"role": role}
    if ref:
        data["ref"] = ref
        data["start_payload"] = ref
    fc = normalize_country(from_country) if from_country else None
    tc = normalize_country(to_country) if to_country else None
    if fc and tc and is_allowed_route(fc, tc):
        data["from_country"] = fc
        data["to_country"] = tc
        set_state(chat_id, "ask_from_city", data)
        msg = (
            f"Маршрут: {fc} → {tc}.\n"
            "Из какого города отправляем?"
        )
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=ReplyKeyboardRemove())
        return
    set_state(chat_id, "choose_route", data)
    role_ru = "получателя" if role == "receiver" else "отправителя"
    msg = (
        f"Оформление заявки (роль: {role_ru}).\n"
        "Выберите направление из 4 доступных маршрутов:"
    )
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=route_keyboard())


def show_allowed_docs(chat_id: int):
    # Prefer plain text version if Markdown fails — send with Markdown first carefully
    text = (
        "Что можно отправлять\n\n"
        "Принимаем документы:\n"
        "• доверенности\n"
        "• дипломы и аттестаты\n"
        "• свидетельства (о рождении, браке и др.)\n"
        "• судебные и нотариальные копии\n"
        "• справки, выписки, договоры (копии)\n"
        "• прочие бумажные документы в конверте до 0,5 кг\n\n"
        "НЕ принимаем: паспорта, ID-карты, национальные удостоверения "
        "личности и аналогичные оригиналы документов, удостоверяющих личность."
    )
    save_message(chat_id, None, text)
    bot.send_message(chat_id, text, reply_markup=main_menu())


def start_track_flow(chat_id: int):
    set_state(chat_id, "await_track", {})
    msg = "Введите номер отправления в формате DBxxxx (например DB1001):"
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=ReplyKeyboardRemove())


def handle_track_ticket(chat_id: int, text: str):
    ticket = (text or "").strip().upper().replace(" ", "")
    if ticket == BTN_MENU.upper() or text.strip() == BTN_MENU:
        go_menu(chat_id)
        return
    if not is_valid_ticket_format(ticket):
        bot.send_message(
            chat_id,
            "Неверный формат. Нужен номер вида DB1001 (DB + цифры). Попробуйте ещё раз или нажмите /start.",
        )
        return
    row = get_shipment_by_ticket(ticket)
    if not row:
        msg = f"Отправление {ticket} не найдено. Проверьте номер или обратитесь к менеджеру."
        save_message(chat_id, text, msg)
        bot.send_message(chat_id, msg, reply_markup=main_menu())
        set_state(chat_id, "greeting", {})
        return

    status = row.get("status") or "accepted"
    status_ru = TRACK_STATUS_RU.get(status, status)
    # Pipeline progress
    lines = [f"Отправление {row['ticket']}", f"Текущий статус: {status_ru}", "", "Этапы:"]
    cur_idx = track_status_index(status)
    for i, st in enumerate(TRACK_STATUSES):
        mark = "✓" if i <= cur_idx else "·"
        lines.append(f"{mark} {TRACK_STATUS_RU.get(st, st)}")
    msg = "\n".join(lines)
    save_message(chat_id, text, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())
    set_state(chat_id, "greeting", {})


def contact_manager(chat_id: int):
    msg = (
        f"Связь с менеджером: {MANAGER_CONTACT}\n"
        "Или оформите заявку через «Отправить документы» — мы свяжемся по телефону."
    )
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())
    set_state(chat_id, "greeting", {})
