# -*- coding: utf-8 -*-
"""DocuBridge dialog flow — Phase 2."""
from typing import Optional, Dict, Any
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from docubridge_runtime import *  # noqa: F401,F403
from docubridge_keyboards import *  # noqa: F401,F403

# ------------ Flow steps ------------

DB_ERROR_TEXT = (
    "⚠️ Техническая заминка на нашей стороне — ваш ответ не потерян, шаг заявки сохранён.\n"
    "Пожалуйста, отправьте это сообщение ещё раз через минуту. "
    "Если не получится — напишите менеджеру: {manager}."
)


def send_db_error(chat_id: int):
    """DB unavailable: tell the user to retry instead of silently resetting the funnel."""
    print(f"[Funnel] chat={chat_id} DB unavailable — asked user to retry (state kept)")
    try:
        bot.send_message(chat_id, DB_ERROR_TEXT.format(manager=MANAGER_CONTACT))
    except Exception as e:
        print(f"[Funnel] send_db_error failed: {e}")


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
        show_route_offer(chat_id, data)
        return
    set_state(chat_id, "choose_route", data)
    role_ru = "получателя" if role == "receiver" else "отправителя"
    msg = (
        f"Оформление заявки (роль: {role_ru}).\n"
        "Выберите направление из 4 доступных маршрутов:"
    )
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=route_keyboard())


def show_route_offer(chat_id: int, data: Dict[str, Any]):
    """First message on a route: offer + «от €140» + ban list, then «Рассчитать точно»."""
    set_state(chat_id, "route_offer", data)
    msg = route_offer_text(data["from_country"], data["to_country"])
    save_message(chat_id, None, msg)
    send_offer_message(chat_id, msg)


def ask_doc_type(chat_id: int, data: Dict[str, Any]):
    set_state(chat_id, "ask_doc_type", data)
    bot.send_message(
        chat_id,
        "1/2. Какой документ отправляем? Выберите кнопкой или «Другое».\n"
        "Паспорта, ID-карты и военные билеты не принимаем.",
        reply_markup=doc_type_keyboard(),
    )


def show_allowed_docs(chat_id: int, reply_markup=None, *, offer: bool = False):
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
        "НЕ принимаем: паспорта, ID-карты, военные билеты, национальные удостоверения "
        "личности и аналогичные оригиналы документов, удостоверяющих личность."
    )
    save_message(chat_id, None, text)
    if offer:
        send_offer_message(chat_id, text)
    else:
        bot.send_message(chat_id, text, reply_markup=reply_markup or main_menu())


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
    status = normalize_status(status) or status
    status_ru = TRACK_STATUS_RU.get(status, status)
    # Pipeline progress
    msg = format_track_message(row, get_shipment_events(row["ticket"]))
    try:
        own = row.get("chat_id") is not None and int(row["chat_id"]) == int(chat_id)
    except (TypeError, ValueError):
        own = False
    if own:   # payment / handover hints only for the order's own chat
        from docubridge_next_steps import track_hint
        import docubridge_lang as lang_mod
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        lang = lang_mod.get_lang(chat_id, default=payload.get("lang") or "ru")
        hint = track_hint(status, payload, lang)
        if hint:
            msg += "\n\n" + hint
    save_message(chat_id, text, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())
    set_state(chat_id, "greeting", {})


def contact_manager(chat_id: int):
    import docubridge_lang as lang_mod
    from docubridge_texts import t as tr
    import docubridge_payment_config as pcfg
    lang = lang_mod.get_lang(chat_id)
    msg = tr("contact_manager", lang, manager=MANAGER_CONTACT, rt=pcfg.RESPONSE_TIME[lang])
    save_message(chat_id, None, msg)
    bot.send_message(chat_id, msg, reply_markup=main_menu())
    set_state(chat_id, "greeting", {})


def _fmt_dt(dt) -> str:
    """dd.mm.yyyy HH:MM in Kyiv time (DB timestamps are timezone-aware)."""
    return fmt_local(dt)


def format_track_message(row: Dict, events) -> str:
    """Stages with dates from shipment_events (latest date per stage)."""
    status = normalize_status(row.get("status")) or "accepted"
    dates, comments = {}, {}
    for ev in events or []:
        st = normalize_status(ev.get("status"))
        if not st:
            continue
        dates[st] = ev.get("created_at")
        if ev.get("comment"):
            comments[st] = ev.get("comment")
    if "accepted" not in dates and row.get("created_at"):
        dates["accepted"] = row.get("created_at")
    lines = [
        f"Отправление {row['ticket']}",
        f"Текущий статус: {TRACK_STATUS_RU.get(status, status)}",
        "",
        "Этапы:",
    ]
    cur_idx = track_status_index(status)
    for i, st in enumerate(TRACK_STATUSES):
        mark = "✓" if i <= cur_idx else "·"
        line = f"{mark} {TRACK_STATUS_RU.get(st, st)}"
        if i <= cur_idx and dates.get(st):
            line += f" — {_fmt_dt(dates[st])}"
        if i <= cur_idx and comments.get(st):
            line += f" ({comments[st]})"
        lines.append(line)
    lines.append(f"Время — {DISPLAY_TZ_LABEL}.")
    return "\n".join(lines)
