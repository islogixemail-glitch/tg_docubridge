# -*- coding: utf-8 -*-
"""DocuBridge dialog — apply/confirm phase — Phase 2."""
from typing import Dict
from telebot.types import ReplyKeyboardRemove

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow_steps import *  # noqa: F401,F403
from docubridge_funnel import *  # noqa: F401,F403
from docubridge_funnel import _off_step_answer
from docubridge_sheets import export_shipment_async
import docubridge_next_steps as ns
from docubridge_handoff import request_handoff
import docubridge_lang as lang_mod
from docubridge_texts import t as tr


def handle_answer_post(chat_id: int, text: str, state: str, data: Dict) -> None:
    s = (text or "").strip()

    if state == "confirm":
        if s == BTN_CANCEL or s.lower() in {"отмена", "нет"}:
            go_menu(chat_id, "Заявка отменена. Вы в главном меню.")
            return
        if s != BTN_CONFIRM and s.lower() not in {"подтвердить", "да", "ok", "ок"}:
            if is_step_question(s):
                prompt_step(chat_id, "confirm", data, prefix=_off_step_answer(s, chat_id, state, data))
                return
            bot.send_message(
                chat_id,
                "Нажмите «Подтвердить», «✏️ Исправить» или «Отмена».",
                reply_markup=confirm_keyboard(),
            )
            return

        lang = lang_mod.get_lang(chat_id)
        data["lang"] = lang                      # kept on the order: status messages use it
        ticket = create_shipment(chat_id, data)
        print(f"[Ticket] chat={chat_id} create_shipment -> {ticket or 'FAILED'} ref={data.get('ref') or '-'}")
        if not ticket:
            print(f"[Ticket] create_shipment failed for chat {chat_id} — no ticket issued")
            notify_admin_order_failed(chat_id, data)
            err = (
                "⚠️ Не удалось сохранить заявку из-за технической ошибки.\n"
                "Номер отправления не присвоен. Попробуйте ещё раз через несколько минут "
                f"или напишите менеджеру: {MANAGER_CONTACT}."
            )
            save_message(chat_id, s, err)
            bot.send_message(chat_id, err, reply_markup=confirm_keyboard())
            return
        data["ticket"] = ticket

        notify_admin_lead(chat_id, data)
        try:
            export_shipment_async(chat_id, data, compute_quote(data).get("price_eur"))
        except Exception as e:  # never break the order
            print(f"[Sheets] export error: {e}")

        thanks = ns.thanks_text(ticket, route_line(data), data, lang)
        save_message(chat_id, s, thanks)
        bot.send_message(chat_id, thanks, reply_markup=main_menu())   # replaces the confirm keys
        try:
            # Terms + «✅ Условия подходят, нужен адрес», then the payment currency (+ actions).
            bot.send_message(chat_id, ns.terms_text(ticket, data, lang), reply_markup=ns.terms_keyboard(ticket, lang))
            bot.send_message(chat_id, ns.currency_question(ticket, data, lang),
                             reply_markup=ns.currency_keyboard(ticket, lang=lang))
        except Exception as e:
            print(f"[NextSteps] follow-up messages failed: {e}")
        set_state(chat_id, "completed", {"ticket": ticket, "from_country": data.get("from_country"),
                                         "to_country": data.get("to_country"), "lang": lang})
        # Only non-standard orders reach a human here: > 0.5 kg (price is discussed with the manager).
        if compute_quote(data).get("price_eur") is None:
            request_handoff(chat_id, "overweight", None, "completed", data, ticket=ticket,
                            scope=f"overweight:{ticket}")
        return

    if state in {"greeting", "completed"} or not state:
        low = s.lower()
        if is_calc_intent(s):
            # «Рассчитать точно», «сколько стоит», «цена» … → straight into the quote.
            route = resolve_start_payload(data.get("ref"))
            if route.get("kind") == "route":
                data["role"] = data.get("role") or "sender"
                data["from_country"] = route["from_country"]
                data["to_country"] = route["to_country"]
                ask_doc_type(chat_id, data)
            else:
                start_send_flow(chat_id, role="sender", ref=data.get("ref"))
            return
        if "отправ" in low or "заявк" in low:
            start_send_flow(chat_id, role="sender", ref=data.get("ref"))
            return
        if "получ" in low:
            start_send_flow(chat_id, role="receiver", ref=data.get("ref"))
            return
        if "отслед" in low or "трек" in low or "status" in low:
            start_track_flow(chat_id)
            return
        asked = looks_like_question(s)
        reply = (ai_reply(s) or "").strip() if asked else ""
        if not reply or reply.lower().startswith(AI_FAIL_PREFIXES):
            reply = FALLBACK_TEXT
            if asked:
                lang = lang_mod.get_lang(chat_id)
                reply = tr("ho_question_menu", lang, manager=MANAGER_CONTACT, rt=ns.rt(lang))
                request_handoff(chat_id, "question", s, state, data)
        else:
            reply += "\n\nМеню: /start, справка: /help."
        save_message(chat_id, s, reply)
        bot.send_message(chat_id, reply, reply_markup=main_menu())
        return

    go_menu(chat_id, "Сессия сброшена. Выберите действие в меню.")
