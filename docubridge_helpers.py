# -*- coding: utf-8 -*-
"""DocuBridge pure helpers (routes, pricing, tickets, volume) — Phase 2."""
import os
import re
import secrets
from typing import Optional, Dict, Tuple, List

ALLOWED_ROUTES = [
    ("Украина", "Россия"),
    ("Украина", "Беларусь"),
    ("Россия", "Украина"),
    ("Беларусь", "Украина"),
]

ROUTE_LABELS = [
    "Украина → Россия",
    "Украина → Беларусь",
    "Россия → Украина",
    "Беларусь → Украина",
]

ROUTE_BY_LABEL = {
    "Украина → Россия": ("Украина", "Россия"),
    "Украина → Беларусь": ("Украина", "Беларусь"),
    "Россия → Украина": ("Россия", "Украина"),
    "Беларусь → Украина": ("Беларусь", "Украина"),
}

# Deep-link /start payloads (site showcase → bot)
START_PAYLOAD_ROUTES = {
    "ua_ru": ("Украина", "Россия"),
    "ua_by": ("Украина", "Беларусь"),
    "ru_ua": ("Россия", "Украина"),
    "by_ua": ("Беларусь", "Украина"),
}


def normalize_start_payload(payload: Optional[str]) -> str:
    return (payload or "").strip().lower()


def resolve_start_payload(payload: Optional[str]) -> Dict:
    """Map /start deep-link payload to an action.

    Returns:
      {"kind": "welcome"} — bare /start
      {"kind": "menu"} — dokumenty or unknown (caller still saves ref)
      {"kind": "route", "from_country", "to_country", "role"} — open quote-first on route
    """
    key = normalize_start_payload(payload)
    if not key:
        return {"kind": "welcome"}
    if key == "dokumenty":
        return {"kind": "menu"}
    route = START_PAYLOAD_ROUTES.get(key)
    if route:
        return {
            "kind": "route",
            "from_country": route[0],
            "to_country": route[1],
            "role": "sender",
        }
    return {"kind": "menu"}

# Display labels (RU) ↔ internal status keys
TRACK_STATUSES: List[str] = [
    "accepted",
    "departed for hub",
    "at hub",
    "destination country",
    "delivered",
]

TRACK_STATUS_RU = {
    "accepted": "принято",
    "departed for hub": "отправлено в хаб",
    "at hub": "на хабе",
    "destination country": "в стране назначения",
    "delivered": "доставлено",
}

# Envelope floor: ≤500 g ordinary → €95; urgent higher; over 0.5 kg → по согласованию
PRICE_ORDINARY_EUR = 95
PRICE_URGENT_EUR = 145
ENVELOPE_MAX_G = 500
ETA_RANGE = "10–14"
TICKET_RE = re.compile(r"^DB\d{4,}$")

MENU_SEND = "Отправить документы"
MENU_RECEIVE = "Получить документы"
MENU_TRACK = "Отследить отправление"
MENU_ALLOWED = "Что можно отправлять"
MENU_MANAGER = "Связаться с менеджером"
BTN_APPLY = "Оформить заявку"
BTN_MENU = "В меню"
BTN_CONFIRM = "Подтвердить"
BTN_CANCEL = "Отмена"

DOC_TYPES_HINT = (
    "доверенность, диплом, свидетельство, судебные/нотариальные копии, "
    "справки, выписки, договоры (копии)"
)

ALLOWED_DOCS_TEXT = (
    "📄 *Что можно отправлять*\n\n"
    "Принимаем документы:\n"
    "• доверенности\n"
    "• дипломы и аттестаты\n"
    "• свидетельства (о рождении, браке и др.)\n"
    "• судебные и нотариальные копии\n"
    "• справки, выписки, договоры (копии)\n"
    "• прочие бумажные документы в конверте до 0,5 кг\n\n"
    "❌ *Не принимаем:* паспорта, ID-карты, национальные удостоверения "
    "личности и аналогичные оригиналы документов, удостоверяющих личность."
)


def normalize_country(x: Optional[str]) -> Optional[str]:
    if not x:
        return None
    s = x.strip().lower()
    mapping = {
        "украина": "Украина",
        "ukraine": "Украина",
        "ua": "Украина",
        "россия": "Россия",
        "rf": "Россия",
        "ru": "Россия",
        "russia": "Россия",
        "беларусь": "Беларусь",
        "рб": "Беларусь",
        "by": "Беларусь",
        "belarus": "Беларусь",
    }
    return mapping.get(s, x.strip().title())


def is_allowed_route(from_country: str, to_country: str) -> bool:
    fc = normalize_country(from_country) or ""
    tc = normalize_country(to_country) or ""
    return (fc, tc) in ALLOWED_ROUTES


def parse_route_label(label: str) -> Optional[Tuple[str, str]]:
    s = (label or "").strip()
    if s in ROUTE_BY_LABEL:
        return ROUTE_BY_LABEL[s]
    # tolerate ascii arrow / dash variants
    for arrow in ("→", "->", "—", "-"):
        if arrow in s:
            parts = [p.strip() for p in s.split(arrow, 1)]
            if len(parts) == 2:
                pair = (normalize_country(parts[0]), normalize_country(parts[1]))
                if pair[0] and pair[1] and is_allowed_route(pair[0], pair[1]):
                    return pair  # type: ignore
    return None


def base_price_eur(weight_grams: int, urgency: str) -> Optional[int]:
    """Return EUR price or None if по согласованию."""
    w = int(weight_grams or 0)
    u = (urgency or "обычная").strip().lower()
    if w <= 0:
        return None
    if w > ENVELOPE_MAX_G:
        return None
    if u == "срочная":
        return PRICE_URGENT_EUR
    return PRICE_ORDINARY_EUR


def eta_working_days(from_country: str, to_country: str) -> Optional[str]:
    """Timeline guide for allowed routes: 10–14 business days."""
    if is_allowed_route(from_country, to_country):
        return ETA_RANGE
    return None


def compute_quote(d: Dict) -> Dict:
    fc = normalize_country(d.get("from_country", "") or "") or ""
    tc = normalize_country(d.get("to_country", "") or "") or ""
    w = int(d.get("weight_grams") or 0)
    urgency = (d.get("urgency") or "обычная").strip().lower()
    if urgency not in {"обычная", "срочная"}:
        urgency = "обычная"

    price = base_price_eur(w, urgency) if w > 0 else None
    eta = eta_working_days(fc, tc)

    if w <= 0:
        notes = "укажите вес или число листов"
    elif w > ENVELOPE_MAX_G:
        notes = "вес > 0,5 кг — стоимость по согласованию"
    elif urgency == "срочная":
        notes = "ускоренная доставка"
    else:
        notes = None

    return {
        "price_eur": price,
        "threshold_g": ENVELOPE_MAX_G if price is not None else None,
        "eta_working": eta,
        "notes": notes,
        "urgency": urgency,
    }


def format_ticket_number(n: int) -> str:
    return f"DB{n:04d}"


def is_valid_ticket_format(ticket: str) -> bool:
    return bool(TICKET_RE.match((ticket or "").strip().upper()))


def generate_ticket_candidate() -> str:
    """DB + at least 4 digits (random). Uniqueness enforced at DB insert."""
    n = secrets.randbelow(900000) + 1000  # 1000..900999 → 4–6 digits
    return f"DB{n}"


def track_status_index(status: str) -> int:
    try:
        return TRACK_STATUSES.index(status)
    except ValueError:
        return 0


def pages_to_grams(pages: int) -> int:
    return max(0, int(pages) * 6)


RUS_NUMS = {
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
    "девяносто": 90, "сто": 100,
}


def parse_int(text: str) -> Optional[int]:
    if not text:
        return None
    s = text.strip().lower()
    m = re.search(r"\d+", s)
    if m:
        try:
            return int(m.group())
        except Exception:
            pass
    tokens = re.findall(r"[а-яё]+", s)
    total = 0
    last = 0
    seen = False
    for t in tokens:
        if t in RUS_NUMS:
            seen = True
            val = RUS_NUMS[t]
            if val >= 20 and val % 10 == 0:
                last = val
            else:
                if last:
                    total += last + val
                    last = 0
                else:
                    total += val
    if seen:
        return total if total > 0 else (last if last > 0 else None)
    return None


def valid_phone(s: str) -> bool:
    s = (s or "").strip().replace(" ", "").replace("-", "")
    return s.startswith("+380") or s.startswith("+7") or s.startswith("+375")


def valid_name(s: str) -> bool:
    s = (s or "").strip()
    return bool(re.match(r"^[A-Za-zА-Яа-яЁё\-'\s]{2,}$", s))


def infer_urgency(text: str) -> Optional[str]:
    s = (text or "").lower()
    urgent_words = [
        "срочно", "срочная", "экспресс", "быстро", "быстрее",
        "urgent", "express", "ускоренная", "ускоренный",
    ]
    ordinary_words = [
        "обычно", "обычная", "стандарт", "стандартный", "базовый",
        "normal", "standard", "без спешки",
    ]
    for w in urgent_words:
        if w in s:
            return "срочная"
    for w in ordinary_words:
        if w in s:
            return "обычная"
    return None


def parse_volume(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Parse volume reply → (pages_a4, weight_grams, error).
    Accepts grams, kg, and/or page counts.
    """
    s = (text or "").strip().lower()
    if not s:
        return None, None, "Укажите вес (например: 300 г) или число листов A4."

    pages = None
    weight = None

    m_kg = re.search(r"(\d+[.,]?\d*)\s*кг", s)
    if m_kg:
        try:
            kg = float(m_kg.group(1).replace(",", "."))
            weight = int(round(kg * 1000))
        except Exception:
            pass

    m_g = re.search(r"(\d+)\s*(?:г|гр|грамм)", s)
    if m_g and weight is None:
        weight = int(m_g.group(1))

    m_p = re.search(r"(\d+)\s*(?:стр|лист|л\b|pages?)", s)
    if m_p:
        pages = int(m_p.group(1))

    if pages is None and weight is None:
        n = parse_int(s)
        if n is not None and n > 0:
            # Ambiguous bare number: treat as pages if small, else grams
            if n <= 200:
                pages = n
                weight = pages_to_grams(n)
            else:
                weight = n
        else:
            return None, None, "Укажите вес в граммах (до 500 г для конверта) или число листов A4."

    if pages is not None and (weight is None or weight == 0):
        weight = pages_to_grams(pages)

    if weight is not None and weight <= 0:
        return None, None, "Вес должен быть больше 0."

    return pages, weight, None
