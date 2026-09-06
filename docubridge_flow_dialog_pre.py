# -*- coding: utf-8 -*-
"""DocuBridge dialog — route/quote phase — Phase 2."""
from typing import Dict
from telebot.types import ReplyKeyboardRemove

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow_steps import *  # noqa: F401,F403


def handle_answer_pre(chat_id: int, text: str, state: str, data: Dict) -> bool:
    """Handle menu + quote wizard. True if done."""
    s = (text or "").strip()

    if s == BTN_MENU or s.lower() in {"/menu", "меню"}:
        go_menu(chat_id)
        return True
    if s == MENU_SEND:
        start_send_flow(chat_id, role="sender", ref=data.get("ref"))
        return True
    if s == MENU_RECEIVE:
        start_send_flow(chat_id, role="receiver", ref=data.get("ref"))
        return True
    if s == MENU_TRACK or s.lower() in {"/track", "track"}:
        start_track_flow(chat_id)
        return True
    if s == MENU_ALLOWED:
        show_allowed_docs(chat_id)
        return True
    if s == MENU_MANAGER:
        contact_manager(chat_id)
        return True
    if state == "await_track":
        handle_track_ticket(chat_id, s)
        return True

    if state == "choose_route":
        pair = parse_route_label(s)
        if not pair:
            bot.send_message(
                chat_id,
                "Пожалуйста, выберите один из 4 маршрутов на клавиатуре. "
                "Маршруты RU↔BY и направления в ЕС недоступны.",
                reply_markup=route_keyboard(),
            )
            return True
        fc, tc = pair
        if not is_allowed_route(fc, tc):
            bot.send_message(
                chat_id,
                "Этот маршрут недоступен. Выберите из 4 разрешённых.",
                reply_markup=route_keyboard(),
            )
            return True
        data["from_country"] = fc
        data["to_country"] = tc
        set_state(chat_id, "ask_from_city", data)
        bot.send_message(
            chat_id,
            f"Маршрут: {fc} → {tc}.\nИз какого города отправляем?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return True

    if state == "ask_from_city":
        if len(s) < 2:
            bot.send_message(chat_id, "Укажите город отправки.")
            return True
        data["from_city"] = s.title()
        set_state(chat_id, "ask_to_city", data)
        bot.send_message(chat_id, "В какой город доставляем?")
        return True

    if state == "ask_to_city":
        if len(s) < 2:
            bot.send_message(chat_id, "Укажите город доставки.")
            return True
        data["to_city"] = s.title()
        set_state(chat_id, "ask_doc_type", data)
        bot.send_message(
            chat_id,
            f"Какой тип документа?\n(например: {DOC_TYPES_HINT})\n"
            "Важно: паспорта и ID-карты не принимаем.",
        )
        return True

    if state == "ask_doc_type":
        low = s.lower()
        banned = ["паспорт", "passport", "id-карт", "id карт", "удостоверен", "national id"]
        if any(b in low for b in banned):
            bot.send_message(
                chat_id,
                "Паспорта, ID-карты и подобные оригиналы не принимаем. "
                "Укажите другой тип документа (доверенность, диплом, свидетельство и т.п.).",
            )
            return True
        if len(s) < 2:
            bot.send_message(chat_id, "Укажите тип документа.")
            return True
        data["doc_type"] = s
        set_state(chat_id, "ask_volume", data)
        bot.send_message(
            chat_id,
            "Укажите объём: вес в граммах (конверт до 500 г) и/или число листов A4.\n"
            "Примеры: «300 г», «40 листов», «0.4 кг».",
        )
        return True

    if state == "ask_volume":
        pages, weight, err = parse_volume(s)
        if err:
            bot.send_message(chat_id, err)
            return True
        if pages is not None:
            data["pages_a4"] = pages
        data["weight_grams"] = weight
        set_state(chat_id, "ask_urgency", data)
        bot.send_message(chat_id, "Срочность доставки?", reply_markup=urgency_keyboard())
        return True

    if state == "ask_urgency":
        u = infer_urgency(s) or (s.lower() if s.lower() in {"обычная", "срочная"} else None)
        if not u:
            bot.send_message(chat_id, "Выберите: обычная или срочная.", reply_markup=urgency_keyboard())
            return True
        data["urgency"] = u
        set_state(chat_id, "quote_shown", data)
        msg = format_quote_message(data)
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=quote_keyboard())
        return True

    return False
