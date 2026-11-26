# -*- coding: utf-8 -*-
"""DocuBridge order funnel: one place for every step's prompt, buttons,
«← Назад», «Связаться с менеджером», off-step questions and «Исправить».

Path: оффер → [Расчёт 1/2] тип → [2/2] вес → цена → «Оформить» →
[Шаг 1–4 из 4] город отправки → город доставки → имя → телефон → подтверждение.
"""
from typing import Dict, Optional

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403
from docubridge_flow_steps import *  # noqa: F401,F403

FUNNEL_STATES = {
    "ask_doc_type", "ask_volume", "quote_shown",
    "ask_from_city", "ask_to_city", "ask_name", "ask_phone",
    "confirm", "edit_choose",
}
# Steps where an abandoned chat gets one reminder.
REMIND_STATES = ["route_offer"] + sorted(FUNNEL_STATES - {"edit_choose"})

BACK_OF = {
    "ask_doc_type": "route_offer",
    "ask_volume": "ask_doc_type",
    "quote_shown": "ask_volume",
    "ask_from_city": "quote_shown",
    "ask_to_city": "ask_from_city",
    "ask_name": "ask_to_city",
    "ask_phone": "ask_name",
    "confirm": "ask_phone",
    "edit_choose": "confirm",
}

APPLY_TOTAL = 4
STEP_TITLES_RU = {
    "route_offer": "оффер",
    "ask_doc_type": "тип документа",
    "ask_volume": "вес",
    "quote_shown": "расчёт цены",
    "ask_from_city": "город отправки",
    "ask_to_city": "город доставки",
    "ask_name": "имя",
    "ask_phone": "телефон",
    "confirm": "подтверждение",
}

MANAGER_INLINE_TEXT = (
    "Менеджер: {manager} — напишите в любой момент.\n"
    "Оформление сохранено, можно продолжить здесь."
)
REMINDER_TEXT = (
    "👋 Вы начали оформление доставки документов, но не закончили "
    "(остановились на шаге «{step}»).\n"
    "Всё сохранено — продолжим с того же места?"
)
PHONE_HELP = (
    "Не получилось распознать номер. Напишите его полностью, например:\n"
    "• +380 67 123 45 67 или 067 123 45 67\n"
    "• +7 916 123-45-67 или 8 916 123-45-67\n"
    "• +375 29 123-45-67\n"
    "Или просто нажмите «📱 Отправить мой номер»."
)


def _country_of(state: str, data: Dict) -> Optional[str]:
    return data.get("from_country") if state == "ask_from_city" else data.get("to_country")


def prompt_step(chat_id: int, state: str, data: Dict, prefix: Optional[str] = None) -> None:
    """Persist `state` and (re)send its question with the right buttons."""
    head = (prefix.strip() + "\n\n") if prefix else ""

    if state == "route_offer" and data.get("from_country") and data.get("to_country"):
        set_state(chat_id, "route_offer", data)
        msg = head + route_offer_text(data["from_country"], data["to_country"])
        save_message(chat_id, None, msg)
        send_offer_message(chat_id, msg)
        return
    if state not in FUNNEL_STATES:
        go_menu(chat_id, (head + "Главное меню. Выберите действие:").strip())
        return

    if state == "confirm" and (not data.get("doc_type") or not data.get("weight_grams")):
        # Legacy chats (old city-first funnel) may miss doc/weight: ask them, then summary.
        data["edit_return"] = True
        data.pop("doc_other", None)
        state = "ask_doc_type"
        head = head + "Остался вопрос о документе — и заявка готова.\n\n"

    set_state(chat_id, state, data)
    fc, tc = data.get("from_country"), data.get("to_country")
    if state == "ask_doc_type":
        if data.get("doc_other"):
            text = f"1/2. Напишите, какой документ (например: {DOC_TYPES_HINT})."
        else:
            text = ("1/2. Какой документ отправляем? Выберите кнопкой или «Другое».\n"
                    "Паспорта, ID-карты и военные билеты не принимаем.")
        kb = doc_type_keyboard()
    elif state == "ask_volume":
        text = ("2/2. Примерный вес конверта? Выберите кнопкой "
                "(или напишите: «300 г», «40 листов»). ~1 лист A4 ≈ 6 г.")
        kb = volume_keyboard()
    elif state == "quote_shown":
        text = format_quote_message(data)
        kb = quote_keyboard()
    elif state == "ask_from_city":
        text = (f"Шаг 1 из {APPLY_TOTAL} · Оформляем заявку ({fc} → {tc}).\n"
                "Из какого города отправляем? Выберите кнопкой или напишите свой город.")
        kb = city_keyboard(fc)
    elif state == "ask_to_city":
        text = (f"Шаг 2 из {APPLY_TOTAL}. В какой город доставляем? "
                "Выберите кнопкой или напишите свой город.")
        kb = city_keyboard(tc)
    elif state == "ask_name":
        tg_name = data.get("tg_name")
        text = f"Шаг 3 из {APPLY_TOTAL}. Как к вам обращаться (имя/фамилия)?"
        if tg_name:
            text += f"\nМожно нажать «{NAME_BTN_PREFIX}{tg_name}» или написать другое имя."
        kb = name_keyboard(tg_name)
    elif state == "ask_phone":
        text = (f"Шаг 4 из {APPLY_TOTAL}. Телефон для связи по заявке: нажмите «{BTN_SHARE_CONTACT}» "
                "или напишите номер, например +380 67 123 45 67 или 067 123 45 67.")
        kb = phone_keyboard()
    elif state == "confirm":
        text = format_confirm_summary(data)
        kb = confirm_keyboard()
    else:  # edit_choose
        text = "Что исправить? Остальные данные сохранятся."
        kb = edit_keyboard()
    msg = head + text
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=kb)


def _after_value(chat_id: int, data: Dict, next_state: str) -> None:
    """Go to the next step, or straight back to the summary when editing."""
    if data.pop("edit_return", False) and next_state != "ask_volume":
        prompt_step(chat_id, "confirm", data)
        return
    prompt_step(chat_id, next_state, data)


def _off_step_answer(s: str, chat_id: Optional[int] = None, state: Optional[str] = None,
                     data: Optional[Dict] = None) -> str:
    """Short answer to a question typed on a data step (we then re-ask the step).
    If the AI cannot answer, the operator gets a «нужен человек» card."""
    low = s.lower()
    if any(w in low for w in STEP_QUESTION_WORDS):
        return step_question_answer()
    reply = ""
    if looks_like_question(s):
        try:
            reply = (ai_reply(s) or "").strip()
        except Exception as e:  # never break the funnel
            print(f"[Funnel] ai_reply error: {e}")
    if not reply or reply.lower().startswith(AI_FAIL_PREFIXES):
        import docubridge_lang as lang_mod
        from docubridge_texts import t as tr
        lang = lang_mod.get_lang(chat_id) if chat_id is not None else "ru"
        import docubridge_payment_config as pcfg
        reply = tr("ho_question_step", lang, manager=MANAGER_CONTACT, rt=pcfg.RESPONSE_TIME[lang])
        if chat_id is not None and looks_like_question(s):
            from docubridge_handoff import request_handoff  # lazy: handoff imports this module
            request_handoff(chat_id, "question", s, state, data)
    return reply[:700]


def handle_funnel(chat_id: int, text: str, state: str, data: Dict) -> bool:
    """Handle a message on a funnel step. False → let the caller handle it."""
    s = (text or "").strip()
    if state not in FUNNEL_STATES:
        return False
    if s in {BTN_MENU, MENU_SEND, MENU_RECEIVE, MENU_TRACK} or s.lower() in {"/menu", "меню", "/track", "track"}:
        return False

    if s == BTN_BACK or (state == "edit_choose" and s == BTN_EDIT_DONE):
        data.pop("edit_return", None)
        data.pop("doc_other", None)
        print(f"[Funnel] chat={chat_id} back {state} -> {BACK_OF[state]}")
        prompt_step(chat_id, BACK_OF[state], data)
        return True
    if s == MENU_MANAGER:
        print(f"[Funnel] chat={chat_id} manager contact on {state} (state kept)")
        prompt_step(chat_id, state, data, prefix=MANAGER_INLINE_TEXT.format(manager=MANAGER_CONTACT))
        return True
    if s == MENU_ALLOWED:
        show_allowed_docs(chat_id, reply_markup=None)
        prompt_step(chat_id, state, data)
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
            prompt_step(chat_id, "ask_doc_type", data)
            return True
        if is_step_question(s):
            prompt_step(chat_id, state, data, prefix=_off_step_answer(s, chat_id, state, data))
            return True
        if len(s) < 2:
            prompt_step(chat_id, state, data, prefix="Укажите тип документа.")
            return True
        data.pop("doc_other", None)
        data["doc_type"] = s
        prompt_step(chat_id, "ask_volume", data)
        return True

    if state == "ask_volume":
        if s in VOLUME_PRESETS:
            pages, weight, err = None, VOLUME_PRESETS[s], None
        elif is_step_question(s) and not any(ch.isdigit() for ch in s):
            prompt_step(chat_id, state, data, prefix=_off_step_answer(s, chat_id, state, data))
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
        if data.pop("edit_return", False):
            prompt_step(chat_id, "confirm", data)
        else:
            prompt_step(chat_id, "quote_shown", data)
        return True

    if state == "quote_shown":
        if s == BTN_APPLY or s.lower() in {"оформить", "заявка", "да"}:
            prompt_step(chat_id, "ask_from_city", data)
            return True
        if is_step_question(s):
            prompt_step(chat_id, state, data, prefix=_off_step_answer(s, chat_id, state, data))
            return True
        bot.send_message(chat_id, "Нажмите «Оформить заявку» или «← Назад».", reply_markup=quote_keyboard())
        return True

    if state in {"ask_from_city", "ask_to_city"}:
        if s == BTN_OTHER_CITY:
            bot.send_message(chat_id, "Напишите название города сообщением.")
            return True
        if is_step_question(s):
            prompt_step(chat_id, state, data, prefix=_off_step_answer(s, chat_id, state, data))
            return True
        if len(s) < 2 or not any(ch.isalpha() for ch in s):
            prompt_step(chat_id, state, data, prefix="Укажите город.")
            return True
        cities = POPULAR_CITIES.get(normalize_country(_country_of(state, data) or "") or "", [])
        city = s if s in cities else s.title()
        if state == "ask_from_city":
            data["from_city"] = city
            _after_value(chat_id, data, "ask_to_city")
        else:
            data["to_city"] = city
            _after_value(chat_id, data, "ask_name")
        return True

    if state == "ask_name":
        tg_name = data.get("tg_name")
        if s.startswith(NAME_BTN_PREFIX):
            s = s[len(NAME_BTN_PREFIX):].strip()
        elif is_step_question(s):
            prompt_step(chat_id, state, data, prefix=_off_step_answer(s, chat_id, state, data))
            return True
        if not valid_name(s):
            print(f"[Funnel] chat={chat_id} name rejected: {mask_name(s)!r} (len {len(s)})")
            prompt_step(chat_id, state, data,
                        prefix="Напишите имя буквами, например: Ірина, Олег Петров, Anna."
                        + (f" Или нажмите «{NAME_BTN_PREFIX}{tg_name}»." if tg_name else ""))
            return True
        data["name"] = s
        _after_value(chat_id, data, "ask_phone")
        return True

    if state == "ask_phone":
        phone = normalize_phone(s)
        if not valid_phone(phone):
            if is_step_question(s) and not any(ch.isdigit() for ch in s):
                prompt_step(chat_id, state, data, prefix=_off_step_answer(s, chat_id, state, data))
                return True
            print(f"[Funnel] chat={chat_id} phone rejected: {mask_phone(s)!r} (digits {sum(c.isdigit() for c in s)})")
            bot.send_message(chat_id, PHONE_HELP, reply_markup=phone_keyboard())
            return True
        data["phone"] = phone
        data.pop("edit_return", None)
        prompt_step(chat_id, "confirm", data)
        return True

    if state == "edit_choose":
        target = EDIT_FIELDS.get(s)
        if not target:
            prompt_step(chat_id, state, data, prefix="Выберите, что исправить, кнопкой ниже.")
            return True
        data["edit_return"] = True
        print(f"[Funnel] chat={chat_id} edit -> {target}")
        prompt_step(chat_id, target, data)
        return True

    if state == "confirm":
        if s == BTN_EDIT or s.lower() in {"исправить", "изменить"}:
            prompt_step(chat_id, "edit_choose", data)
            return True
        return False  # confirm / cancel handled in docubridge_flow_dialog_post

    return False
