# -*- coding: utf-8 -*-
"""DocuBridge dialog — apply/confirm phase — Phase 2."""
from typing import Dict
from telebot.types import ReplyKeyboardRemove

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow_steps import *  # noqa: F401,F403


def handle_answer_post(chat_id: int, text: str, state: str, data: Dict) -> None:
    s = (text or "").strip()

    if state == "quote_shown":
        if s == BTN_APPLY or s.lower() in {"оформить", "заявка", "да"}:
            set_state(chat_id, "ask_name", data)
            bot.send_message(
                chat_id,
                "Как к вам обращаться (имя/фамилия)?",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        if s == BTN_MENU:
            go_menu(chat_id)
            return
        bot.send_message(
            chat_id,
            "Нажмите «Оформить заявку» или «В меню».",
            reply_markup=quote_keyboard(),
        )
        return

    if state == "ask_name":
        if not valid_name(s):
            bot.send_message(
                chat_id,
                "Введите имя/фамилию (буквы, пробелы и дефисы; не короче 2 символов).",
            )
            return
        data["name"] = s
        set_state(chat_id, "ask_phone", data)
        bot.send_message(chat_id, "Контактный телефон (+380 / +7 / +375):")
        return

    if state == "ask_phone":
        phone = s.replace(" ", "").replace("-", "")
        if not valid_phone(phone):
            bot.send_message(chat_id, "Телефон должен начинаться с +380 / +7 / +375.")
            return
        data["phone"] = phone
        set_state(chat_id, "confirm", data)
        msg = format_confirm_summary(data)
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=confirm_keyboard())
        return

    if state == "confirm":
        if s == BTN_CANCEL or s.lower() in {"отмена", "нет"}:
            go_menu(chat_id, "Заявка отменена. Вы в главном меню.")
            return
        if s != BTN_CONFIRM and s.lower() not in {"подтвердить", "да", "ok", "ок"}:
            bot.send_message(
                chat_id,
                "Нажмите «Подтвердить» или «Отмена».",
                reply_markup=confirm_keyboard(),
            )
            return

        ticket = create_shipment(chat_id, data)
        if not ticket:
            ticket = generate_ticket_candidate()
            data["ticket"] = ticket
            print(f"[Ticket] Fallback ticket {ticket} (DB insert failed)")
        else:
            data["ticket"] = ticket

        notify_admin_lead(chat_id, data)

        thanks = (
            f"✅ Заявка оформлена.\n"
            f"Номер отправления: {ticket}\n\n"
            f"Маршрут: {route_line(data)}\n"
            f"Мы свяжемся с вами по телефону {data.get('phone')}.\n"
            f"Статус можно проверить: «Отследить отправление» или /track."
        )
        save_message(chat_id, s, thanks)
        bot.send_message(chat_id, thanks, reply_markup=main_menu())
        set_state(chat_id, "completed", {"ticket": ticket})
        return

    if state in {"greeting", "completed"} or not state:
        low = s.lower()
        if "отправ" in low or "заявк" in low or "расчёт" in low or "расчет" in low:
            start_send_flow(chat_id, role="sender", ref=data.get("ref"))
            return
        if "получ" in low:
            start_send_flow(chat_id, role="receiver", ref=data.get("ref"))
            return
        if "отслед" in low or "трек" in low or "status" in low:
            start_track_flow(chat_id)
            return
        reply = ai_reply(s)
        save_message(chat_id, s, reply)
        bot.send_message(chat_id, reply, reply_markup=main_menu())
        return

    go_menu(chat_id, "Сессия сброшена. Выберите действие в меню.")
