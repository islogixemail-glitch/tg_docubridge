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
    lines = [f"Последние заявки ({len(rows)}), время — {DISPLAY_TZ_LABEL}:", ""]
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
        f"Создана: {_fmt_dt(row.get('created_at'))} ({DISPLAY_TZ_LABEL})",
        f"Обновлена: {_fmt_dt(row.get('updated_at'))} ({DISPLAY_TZ_LABEL})",
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


def client_status_message(ticket: str, status: str, comment: Optional[str], lang: str = "ru") -> str:
    from docubridge_next_steps import client_status_message as _msg
    return _msg(ticket, status, comment, lang)


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
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        lang = (payload or {}).get("lang") or "ru"
        bot.send_message(int(row["chat_id"]), client_status_message(row["ticket"], status, comment, lang))
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


# «Документы получены — запросить оплату». The existing statuses do not say «arrived in
# Bratislava / at the Moscow agent» unambiguously, so this is a separate admin action:
# button under the lead card (st:<ticket>:payreq) and /payreq DB1234.
PAYREQ_CB_STATUS = "payreq"


def request_payment(ticket: str):
    """Send the client «документы поступили — нужна оплата» + details of the chosen currency
    (or the currency choice / fallback). Returns (ok, message_for_admin). No status change."""
    import docubridge_next_steps as ns
    import docubridge_db_notify as ndb
    import docubridge_lang as lang_mod
    ticket = (ticket or "").strip().upper()
    if not is_valid_ticket_format(ticket):
        return False, "Неверный номер. Формат: DB1234"
    row = ndb.get_order_pay_info(ticket)
    if not row:
        return False, f"Заявка {ticket} не найдена (или БД недоступна)."
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    chat_id = int(row["chat_id"])
    lang = lang_mod.norm(payload.get("lang") or lang_mod.get_lang(chat_id))
    cur = row.get("pay_currency")
    text = ns.payment_request_text(row["ticket"], payload, cur, lang)
    kb = (ns.after_payment_keyboard(row["ticket"], lang) if cur
          else ns.currency_keyboard(row["ticket"], with_actions=False, lang=lang))
    try:
        bot.send_message(chat_id, text, reply_markup=kb)
    except Exception as e:
        print(f"[ADMIN payreq] client notify error: {e}")
        return False, f"Не удалось отправить клиенту просьбу об оплате по {row['ticket']}."
    if ndb.mark_pay_requested(row["ticket"]) is not True:
        print(f"[ADMIN payreq] {row['ticket']}: pay_requested_at not stored")
        not_saved = "\n⚠️ Отметка «оплата запрошена» не сохранена в БД — реквизиты по кнопке валюты клиент не получит, пришлите вручную."
    else:
        not_saved = ""
    if cur and not ns.pcfg.payment_text_for(cur):
        note = f"⚠️ {row['ticket']}: клиент выбрал {cur}, реквизитов нет, пришлите вручную"
    else:
        note = ""
    who = f"валюта: {cur}" if cur else "валюта не выбрана — клиенту показан выбор валюты"
    msg = f"✅ {row['ticket']}: клиенту отправлена просьба об оплате ({who})."
    return True, msg + (f"\n{note}" if note else "") + not_saved


@bot.message_handler(commands=["payreq"], func=lambda m: is_admin(m.chat.id))
def admin_payreq(message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Использование: /payreq DB1234 — документы получены, запросить оплату")
        return
    ok, msg = request_payment(parts[1])
    bot.send_message(message.chat.id, msg)


def can_press_status(user_id) -> bool:
    """By the presser's user id (not the chat): admin + LEADS_MANAGER_IDS."""
    from docubridge_notify import can_manage
    return is_admin(user_id) or can_manage(user_id)


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith(STATUS_CB_PREFIX + ":"))
def admin_status_button(call):
    uid = getattr(call.from_user, "id", None)
    if not can_press_status(uid):
        print(f"[ADMIN status] denied for user {uid}")
        bot.answer_callback_query(call.id, "Нет доступа")
        return
    try:
        _, ticket, status = call.data.split(":", 2)
    except ValueError:
        bot.answer_callback_query(call.id, "Некорректные данные")
        return
    if status == PAYREQ_CB_STATUS:
        ok, msg = request_payment(ticket)
    else:
        ok, msg = apply_status_change(ticket, status)
    bot.answer_callback_query(call.id, msg[:190])
    if call.message:
        if getattr(call.message.chat, "type", "private") != "private":
            u = call.from_user
            who = ("@" + u.username) if getattr(u, "username", None) else (getattr(u, "first_name", None) or str(uid))
            msg = f"{msg} — {who}"
        bot.send_message(call.message.chat.id, msg)


STATE_LABELS_RU = {
    "greeting": "меню/приветствие",
    "choose_route": "выбор маршрута",
    "route_offer": "оффер (не нажал «Рассчитать»)",
    "ask_doc_type": "тип документа",
    "ask_volume": "вес",
    "quote_shown": "цена показана (не нажал «Оформить»)",
    "ask_from_city": "город отправки",
    "ask_to_city": "город доставки",
    "ask_name": "имя",
    "ask_phone": "телефон",
    "confirm": "подтверждение",
    "edit_choose": "исправление заявки",
    "completed": "заявка оформлена",
    "await_track": "ввод номера для отслеживания",
}

STATS_PERIODS = (1, 7, 30)


def format_stats(periods, detail) -> str:
    """periods: list of stats dicts (short lines); detail: stats dict for breakdowns."""
    if not detail:
        return "Статистика недоступна: нет подключения к БД."
    lines = ["📊 DocuBridge — воронка", ""]
    for st in periods:
        if not st:
            continue
        tracked = st.get("orders_tracked", st["orders"])
        conv = f"{min(tracked / st['start_chats'], 1) * 100:.0f}%" if st["start_chats"] else "—"
        extra = f" (из них после старта с меткой/логом: {tracked})" if tracked != st["orders"] else ""
        rem = f", напоминаний {st['reminders']}" if st.get("reminders") else ""
        lines.append(
            f"За {st['days']} дн.: стартов {st['starts']} (чатов {st['start_chats']}), "
            f"заявок {st['orders']}{extra}, конверсия {conv}{rem}"
        )
    d = detail["days"]
    lines += ["", f"Старты по меткам ({d} дн.):"]
    lines += [f"• {p}: {n}" for p, n in detail["by_payload"]] or ["• —"]
    lines += ["", f"Заявки по маршрутам ({d} дн.):"]
    lines += [f"• {r}: {n}" for r, n in detail["by_route"]] or ["• —"]
    lines += ["", f"Где сейчас пользователи (последний шаг, активность за {d} дн.):"]
    lines += [f"• {STATE_LABELS_RU.get(s, s)}: {n}" for s, n in detail["by_state"]] or ["• —"]
    lines += ["", "Старты учитываются с момента включения логирования /start; "
                  "конверсия = заявки чатов с залогированным стартом / чаты со стартом."]
    return "\n".join(lines)


@bot.message_handler(commands=["stats"], func=lambda m: is_admin(m.chat.id))
def admin_stats(message):
    parts = (message.text or "").split()
    custom = None
    if len(parts) > 1 and parts[1].isdigit() and 0 < int(parts[1]) <= 365:
        custom = int(parts[1])
    days_list = (custom,) if custom else STATS_PERIODS
    periods = [get_funnel_stats(d) for d in days_list]
    detail = periods[-1]
    bot.send_message(message.chat.id, format_stats(periods, detail))
