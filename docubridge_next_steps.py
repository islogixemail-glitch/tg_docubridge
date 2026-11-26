# -*- coding: utf-8 -*-
"""After the order DBxxxx: «Что дальше», terms + «нужен адрес», currency choice,
payment / handover answers, status explanations, /track hint — RU and UA.

Facts come only from helpers (price, envelope limit, ETA) and docubridge_payment_config
(owner-approved: handover by origin, 100% prepayment after arrival in Bratislava / Moscow,
UAH details, 24 h). Templates live in docubridge_texts. Nothing is invented here.
"""
import re
from typing import Dict, Optional

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from docubridge_config import MANAGER_CONTACT
import docubridge_payment_config as pcfg
from docubridge_texts import t, STATUS_UK
from docubridge_helpers import (
    compute_quote, ENVELOPE_MAX_G, PRICE_ORDINARY_EUR, TRACK_STATUS_RU, normalize_status,
)

NS_CB_PREFIX = "ns"                 # ns:<action>:<ticket>
NS_ACTIONS = ("pay", "hand", "track", "human")   # pay = (re)show the currency choice
PC_CB_PREFIX = "pc"                 # pc:<ticket>:<CUR> — currency chosen
TC_CB_PREFIX = "tc"                 # tc:<ticket> — terms accepted, address needed


def rt(lang: str) -> str:
    return pcfg.RESPONSE_TIME.get(lang, pcfg.RESPONSE_TIME["ru"])


def origin(data: Optional[Dict]) -> Optional[str]:
    return pcfg.origin_of((data or {}).get("from_country"))


def _quote(data: Dict) -> Dict:
    return compute_quote(data or {})


def price_line(data: Dict, lang: str = "ru") -> str:
    q = _quote(data)
    if q.get("price_eur") is not None:
        return t("price", lang, price=q["price_eur"], max_g=ENVELOPE_MAX_G)
    return t("price_over", lang, rt=rt(lang))


def eta_line(data: Dict, lang: str = "ru") -> str:
    q = _quote(data)
    return t("eta", lang, eta=q["eta_working"]) if q.get("eta_working") else t("eta_none", lang)


def thanks_text(ticket: str, route: str, data: Dict, lang: str = "ru") -> str:
    o = origin(data)
    return t("thanks", lang, ticket=ticket, route=route, price_line=price_line(data, lang),
             eta_line=eta_line(data, lang), handover=pcfg.handover_text(o, lang),
             pct=pcfg.PREPAYMENT_PERCENT, arrival=pcfg.arrival(o, lang), manager=MANAGER_CONTACT)


def terms_text(ticket: str, data: Dict, lang: str = "ru") -> str:
    q, o = _quote(data), origin(data)
    price_short = (t("price_short", lang, price=q["price_eur"], max_g=ENVELOPE_MAX_G)
                   if q.get("price_eur") is not None else t("price_short_over", lang))
    eta_short = (t("eta_short", lang, eta=q["eta_working"]) if q.get("eta_working")
                 else t("eta_short_none", lang))
    return t("terms", lang, ticket=ticket, price_short=price_short, eta_short=eta_short,
             ship_to=pcfg.ship_to(o, lang) or t("ship_to_any", lang),
             pct=pcfg.PREPAYMENT_PERCENT, arrival=pcfg.arrival(o, lang))


def terms_keyboard(ticket: str, lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(t("btn_terms", lang), callback_data=tc_callback(ticket)))
    return kb


# ------------ callbacks ------------

def ns_callback(action: str, ticket: str) -> str:
    return f"{NS_CB_PREFIX}:{action}:{ticket}"


def parse_ns_callback(data: Optional[str]):
    """'ns:pay:DB1234' → ('pay', 'DB1234'); None if not ours."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != NS_CB_PREFIX or parts[1] not in NS_ACTIONS:
        return None
    return parts[1], parts[2]


def pc_callback(ticket: str, cur: str) -> str:
    return f"{PC_CB_PREFIX}:{ticket}:{cur}"


def parse_pc_callback(data: Optional[str]):
    """'pc:DB1234:UAH' → ('DB1234', 'UAH'); None if not ours (currency validity checked later)."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != PC_CB_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2].upper()


def tc_callback(ticket: str) -> str:
    return f"{TC_CB_PREFIX}:{ticket}"


def parse_tc_callback(data: Optional[str]) -> Optional[str]:
    parts = (data or "").split(":", 1)
    if len(parts) != 2 or parts[0] != TC_CB_PREFIX or not parts[1]:
        return None
    return parts[1]


# ------------ currency / payment ------------

def currency_question(ticket: str, data: Optional[Dict] = None, lang: str = "ru") -> str:
    return t("cur_question", lang, ticket=ticket, arrival=pcfg.arrival(origin(data), lang))


def currency_keyboard(ticket: str, with_actions: bool = True, lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(*[InlineKeyboardButton(c, callback_data=pc_callback(ticket, c)) for c in pcfg.currencies()])
    if with_actions:
        kb.row(InlineKeyboardButton(t("btn_handover", lang), callback_data=ns_callback("hand", ticket)))
        kb.row(InlineKeyboardButton(t("btn_track", lang), callback_data=ns_callback("track", ticket)))
        kb.row(InlineKeyboardButton(t("btn_human", lang), callback_data=ns_callback("human", ticket)))
    return kb


def next_steps_keyboard(ticket: str, lang: str = "ru") -> InlineKeyboardMarkup:
    return currency_keyboard(ticket, lang=lang)


def after_payment_keyboard(ticket: str, lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(t("btn_change_cur", lang), callback_data=ns_callback("pay", ticket)))
    kb.row(InlineKeyboardButton(t("btn_handover", lang), callback_data=ns_callback("hand", ticket)))
    kb.row(InlineKeyboardButton(t("btn_track", lang), callback_data=ns_callback("track", ticket)))
    kb.row(InlineKeyboardButton(t("btn_human", lang), callback_data=ns_callback("human", ticket)))
    return kb


def pay_button_keyboard(ticket: str, lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(t("btn_pay", lang), callback_data=ns_callback("pay", ticket)))
    return kb


def currency_saved_text(ticket: str, cur: str, data: Optional[Dict] = None, lang: str = "ru") -> str:
    """Currency chosen before the documents arrived: saved, NO payment details yet."""
    return t("cur_saved", lang, cur=cur, arrival=pcfg.arrival(origin(data), lang))


def group_currency_note(ticket: str, cur: str, stored, requested: bool = False) -> str:
    """Group note on a currency choice. Before /payreq nothing has been sent to the client."""
    tail = "" if stored else "\n(валюта не сохранена в БД — БД недоступна)"
    if requested and not pcfg.payment_text_for(cur):
        return f"⚠️ {ticket}: клиент выбрал {cur}, реквизитов нет, пришлите вручную{tail}"
    return f"💱 {ticket}: клиент выбрал {cur}{tail}"


def payment_answer(ticket: Optional[str] = None, currency: Optional[str] = None,
                   data: Optional[Dict] = None, lang: str = "ru", requested: bool = False) -> str:
    """Text answer to «как оплатить?». Details only after the admin payment request (/payreq)."""
    if ticket and currency:
        if requested:
            return payment_request_text(ticket, data, currency, lang)
        return currency_saved_text(ticket, currency, data, lang)
    return t("pay_preorder", lang, pct=pcfg.PREPAYMENT_PERCENT, arrival_any=pcfg.ARRIVAL_ANY[lang])


def payment_request_text(ticket: str, data: Optional[Dict], cur: Optional[str], lang: str = "ru") -> str:
    """Admin «документы получены — запросить оплату» → client."""
    o = origin(data)
    arr = pcfg.ARRIVAL[o][lang] if o in pcfg.ARRIVAL else t("payreq_arrived_any", lang)
    if cur and pcfg.payment_text_for(cur):
        body = t("payreq_details", lang, cur=cur, details=pcfg.payment_text_for(cur))
    elif cur:
        body = t("payreq_fallback", lang, cur=cur, rt=rt(lang))
    else:
        body = t("payreq_choose", lang)
    return t("payreq", lang, ticket=ticket, arrival=arr, pct=pcfg.PREPAYMENT_PERCENT, body=body)


# ------------ handover / address ------------

def handover_answer(ticket: Optional[str] = None, data: Optional[Dict] = None, lang: str = "ru") -> str:
    o = origin(data)
    note = (t("addr_note_order", lang, btn=t("btn_terms", lang)) if ticket
            else t("addr_note_preorder", lang))
    for_ticket = t("for_ticket", lang, ticket=ticket) if ticket else ""
    return t("handover", lang, for_ticket=for_ticket, handover=pcfg.handover_text(o, lang), addr_note=note)


def address_answer(ticket: Optional[str], lang: str = "ru") -> str:
    return t("addr_order", lang) if ticket else t("addr_preorder", lang)


def address_card(ticket: str, payload: Dict, who: str, chat_id: int, accepted_at: str, lang: str) -> str:
    o = origin(payload)
    from docubridge_keyboards import route_line
    lines = [
        f"📬 {ticket}: клиент согласен, нужен адрес ({pcfg.ADDRESS_TAG.get(o, 'маршрут не определён')})",
        f"Маршрут: {route_line(payload or {})}",
        f"Куда нужен адрес: {pcfg.ADDRESS_PLACE.get(o, '—')}",
        f"Клиент: {who}",
        f"Chat ID: {chat_id}",
        f"Язык клиента: {'UA' if lang == 'uk' else 'RU'}",
        f"Условия приняты: {accepted_at}",
        f"Карточка заявки: /order {ticket}",
    ]
    return "\n".join(lines)


# ------------ statuses / track ------------

def status_name(status: str, lang: str = "ru") -> str:
    st = normalize_status(status) or status
    return (STATUS_UK if lang == "uk" else TRACK_STATUS_RU).get(st, st)


def status_next(status: Optional[str], lang: str = "ru") -> Optional[str]:
    st = normalize_status(status)
    return t(f"st_next_{st}", lang) if st else None


def client_status_message(ticket: str, status: str, comment: Optional[str], lang: str = "ru") -> str:
    msg = t("st_head", lang, ticket=ticket, status=status_name(status, lang))
    if comment:
        msg += "\n" + t("st_comment", lang, comment=comment)
    nxt = status_next(status, lang)
    if nxt:
        msg += "\n" + nxt
    return msg + "\n" + t("st_more", lang, ticket=ticket)


def track_hint(status: Optional[str], data: Optional[Dict] = None, lang: str = "ru") -> Optional[str]:
    """Extra lines under /track for the order's own chat."""
    st = normalize_status(status) or "accepted"
    o = origin(data)
    if st == "accepted":
        return t("track_hint_accepted", lang, handover=pcfg.handover_text(o, lang),
                 pct=pcfg.PREPAYMENT_PERCENT, arrival=pcfg.arrival(o, lang))
    nxt = status_next(st, lang)
    return t("track_hint_other", lang, next=nxt) if nxt else None


# ------------ FAQ intents answered without AI ------------

ADDRESS_RE = re.compile(r"адрес|куда (?:отправ|присыл|слать|высыл)|куди (?:надісл|відправ|надсил)")
PAYMENT_RE = re.compile(r"оплат|оплач|реквизит|реквізит|заплат|платить|платити|сплат|плачу|предоплат|передоплат"
                        r"|сч[её]т на|iban|\bкарт(?:ой|у|ою)\b")
HANDOVER_RE = re.compile(
    r"передать документ|передач[аиу] документ|передати документ|як передати|как передать"
    r"|куда (?:принести|привезти|отнести)|куди (?:принести|привезти|віднести)"
    r"|забер[её]те|заберете|курь?ер|кур.?єр|нов[аой]+ пошт|почтой|поштою|сдэк|сдек"
)


def faq_intent(text: Optional[str]) -> Optional[str]:
    """'address' | 'payment' | 'handover' | None."""
    low = (text or "").lower()
    if ADDRESS_RE.search(low):
        return "address"
    if PAYMENT_RE.search(low):
        return "payment"
    if HANDOVER_RE.search(low):
        return "handover"
    return None
