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
        if state == "route_offer":
            show_allowed_docs(chat_id, reply_markup=offer_keyboard())
        else:
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
        show_route_offer(chat_id, data)
        return True

    if state == "route_offer":
        low = s.lower()
        if s == BTN_CALC or "рассчит" in low or "расчет" in low or "расчёт" in low or low in {"да", "ок", "ok"}:
            ask_doc_type(chat_id, data)
            return True
        reply = step_question_answer() if is_step_question(s) else ""
        bot.send_message(
            chat_id,
            (reply + "\n\n" if reply else "") + "Нажмите «Рассчитать точно», чтобы получить цену.",
            reply_markup=offer_keyboard(),
        )
        return True

    if state == "ask_doc_type":
        if is_banned_doc(s):
            bot.send_message(
                chat_id,
                "Паспорта, ID-карты, военные билеты и подобные оригиналы не принимаем. "
                "Выберите другой тип документа.",
                reply_markup=doc_type_keyboard(),
            )
            return True
        if s == DOC_OTHER:
            data["doc_other"] = True
            set_state(chat_id, "ask_doc_type", data)
            bot.send_message(
                chat_id,
                f"Напишите, какой документ (например: {DOC_TYPES_HINT}).",
                reply_markup=ReplyKeyboardRemove(),
            )
            return True
        if is_step_question(s):
            bot.send_message(
                chat_id,
                step_question_answer() + "\n\nКакой документ отправляем?",
                reply_markup=doc_type_keyboard(),
            )
            return True
        if len(s) < 2:
            bot.send_message(chat_id, "Укажите тип документа.", reply_markup=doc_type_keyboard())
            return True
        data.pop("doc_other", None)
        data["doc_type"] = s
        set_state(chat_id, "ask_volume", data)
        bot.send_message(
            chat_id,
            "2/2. Примерный вес конверта? Выберите кнопкой "
            "(или напишите: «300 г», «40 листов»). ~1 лист A4 ≈ 6 г.",
            reply_markup=volume_keyboard(),
        )
        return True

    if state == "ask_volume":
        if s in VOLUME_PRESETS:
            pages, weight, err = None, VOLUME_PRESETS[s], None
        elif is_step_question(s) and not any(ch.isdigit() for ch in s):
            bot.send_message(
                chat_id,
                step_question_answer() + "\n\nВыберите вес кнопкой.",
                reply_markup=volume_keyboard(),
            )
            return True
        else:
            pages, weight, err = parse_volume(s)
        if err:
            bot.send_message(chat_id, err, reply_markup=volume_keyboard())
            return True
        data.pop("pages_a4", None)
        if pages is not None:
            data["pages_a4"] = pages
        data["weight_grams"] = weight
        data["urgency"] = "обычная"
        set_state(chat_id, "quote_shown", data)
        msg = format_quote_message(data)
        save_message(chat_id, None, msg)
        bot.send_message(chat_id, msg, reply_markup=quote_keyboard())
        return True

    if state in {"ask_from_city", "ask_to_city"}:
        prompt = "Из какого города отправляем?" if state == "ask_from_city" else "В какой город доставляем?"
        if is_step_question(s):
            bot.send_message(chat_id, step_question_answer() + "\n\n" + prompt)
            return True
        if len(s) < 2 or not any(ch.isalpha() for ch in s):
            bot.send_message(chat_id, "Укажите город. " + prompt)
            return True
        if state == "ask_from_city":
            data["from_city"] = s.title()
            set_state(chat_id, "ask_to_city", data)
            bot.send_message(chat_id, "В какой город доставляем?")
            return True
        data["to_city"] = s.title()
        set_state(chat_id, "ask_name", data)
        bot.send_message(chat_id, "Как к вам обращаться (имя/фамилия)?")
        return True

    return False
