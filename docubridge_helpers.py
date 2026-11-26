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


HUB_CB_PREFIX = "hub"
KNOWN_COMMANDS = {"start", "help", "track", "menu", "reset", "ai", "orders", "order", "status", "stats", "payreq"}
BOTFATHER_COMMANDS = [
    ("start", "Главное меню и выбор маршрута"),
    ("help", "Справка: что делает бот и как получить расчёт"),
    ("track", "Отследить отправление по номеру DBxxxx"),
    ("menu", "Вернуться в главное меню"),
]

HELP_TEXT = (
    "DocuBridge — доставка бумажных документов по маршрутам:\n"
    "• Украина → Россия\n"
    "• Украина → Беларусь\n"
    "• Россия → Украина\n"
    "• Беларусь → Украина\n\n"
    "Как получить расчёт:\n"
    "1. Нажмите /start и выберите маршрут.\n"
    "2. Укажите города, тип документа, вес или число листов.\n"
    "3. Бот сразу покажет ориентировочную цену и срок. Контакты — только если решите оформить заявку.\n\n"
    "Отслеживание: /track и номер DBxxxx.\n\n"
    "Не принимаем: паспорта, ID-карты, военные билеты и другие документы, удостоверяющие личность.\n\n"
    "Команды: /start — меню, /help — справка, /track — отслеживание, /menu — главное меню."
)

FALLBACK_TEXT = (
    "Извините, я не понял сообщение.\n"
    "Выберите действие в меню ниже или нажмите /start (выбор маршрута) или /help (справка)."
)


def command_name(text: Optional[str]) -> Optional[str]:
    """'/Help@islogix_bot arg' -> 'help'; None if not a command."""
    s = (text or "").strip()
    if not s.startswith("/") or len(s) < 2:
        return None
    return s[1:].split(maxsplit=1)[0].split("@", 1)[0].lower()


def is_unknown_command(text: Optional[str]) -> bool:
    name = command_name(text)
    return name is not None and name not in KNOWN_COMMANDS


def looks_like_question(text: Optional[str]) -> bool:
    """Heuristic: real question worth sending to AI vs. gibberish."""
    s = (text or "").strip()
    words = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]{2,}", s)
    if len(words) < 2:
        return False
    vowels = set("аеёиоуыэюяіїєaeiouy")
    letters = [c for c in s.lower() if c.isalpha()]
    return bool(letters) and sum(c in vowels for c in letters) / len(letters) >= 0.2


def hub_callback_data(route_key: str) -> str:
    return f"{HUB_CB_PREFIX}:{route_key}"


def parse_hub_callback(data: Optional[str]) -> Optional[Tuple[str, str]]:
    s = data or ""
    if not s.startswith(HUB_CB_PREFIX + ":"):
        return None
    return START_PAYLOAD_ROUTES.get(s.split(":", 1)[1])


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
    "departed_hub",
    "at_hub",
    "destination_country",
    "delivered",
]

TRACK_STATUS_RU = {
    "accepted": "принято",
    "departed_hub": "отправлено в хаб",
    "at_hub": "на хабе",
    "destination_country": "в стране назначения",
    "delivered": "доставлено",
}


def normalize_status(status: Optional[str]) -> Optional[str]:
    """Map legacy keys ('at hub') / RU labels to canonical keys; None if unknown."""
    if not status:
        return None
    s = str(status).strip().lower().replace(" ", "_").replace("-", "_")
    if s in TRACK_STATUSES:
        return s
    for k, ru in TRACK_STATUS_RU.items():
        if ru.replace(" ", "_") == s:
            return k
    return None

# Envelope floor: ≤500 g → €140 (single tariff, no urgent); over 0.5 kg → по согласованию
PRICE_ORDINARY_EUR = 140
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
BTN_CALC = "Рассчитать точно"
OFFER_CB_CALC = "offer_calc"
OFFER_CB_ALLOWED = "offer_allowed"
OFFER_CB_MENU = "offer_menu"
OFFER_CALLBACKS = (OFFER_CB_CALC, OFFER_CB_ALLOWED, OFFER_CB_MENU)
BTN_SHARE_CONTACT = "📱 Отправить мой номер"
BTN_BACK = "← Назад"
BTN_EDIT = "✏️ Исправить"
BTN_OTHER_CITY = "✏️ Другой город"
NAME_BTN_PREFIX = "✅ "
EDIT_FIELDS = {
    "Город отправки": "ask_from_city",
    "Город доставки": "ask_to_city",
    "Имя": "ask_name",
    "Телефон": "ask_phone",
    "Тип документа и вес": "ask_doc_type",
}
BTN_EDIT_DONE = "← Назад к заявке"
POPULAR_CITIES = {
    "Украина": ["Киев", "Одесса", "Харьков", "Днепр", "Львов", "Запорожье"],
    "Россия": ["Москва", "Санкт-Петербург", "Краснодар", "Ростов-на-Дону", "Екатеринбург", "Новосибирск"],
    "Беларусь": ["Минск", "Гомель", "Брест", "Гродно", "Витебск", "Могилёв"],
}
DOC_OTHER = "Другое"
DOC_TYPE_BUTTONS = [
    "Доверенность", "Диплом / аттестат", "Свидетельство",
    "Справка / выписка", "Договор (копия)", DOC_OTHER,
]
VOLUME_PRESETS = {
    "до 100 г": 100,
    "до 300 г": 300,
    "до 500 г": 500,
    "больше 500 г": ENVELOPE_MAX_G + 1,
}
BANNED_DOC_WORDS = [
    "паспорт", "passport", "id-карт", "id карт", "айди", "удостоверен",
    "national id", "военн", "военник",
]
CALC_INTENT_WORDS = ("рассчит", "расчет", "расчёт", "цена", "цену", "стоимост", "сколько", "почём", "почем", "тариф")
STEP_QUESTION_WORDS = ("сколько", "цена", "цену", "стоимост", "стоит", "почём", "почем", "тариф")


def is_banned_doc(text: Optional[str]) -> bool:
    low = (text or "").lower()
    return any(b in low for b in BANNED_DOC_WORDS)


def is_calc_intent(text: Optional[str]) -> bool:
    """Greeting-state text that means «покажите цену / рассчитайте»."""
    low = (text or "").strip().lower()
    return bool(low) and any(w in low for w in CALC_INTENT_WORDS)


def is_step_question(text: Optional[str]) -> bool:
    """Input on a data step (city, name...) that is really a question, not data."""
    s = (text or "").strip().lower()
    if not s:
        return False
    if "?" in s or "？" in s:
        return True
    return any(w in s for w in STEP_QUESTION_WORDS)


def route_offer_text(from_country: str, to_country: str) -> str:
    return (
        f"Маршрут: {from_country} → {to_country}\n\n"
        f"📦 Доставка бумажных документов — от €{PRICE_ORDINARY_EUR} "
        f"(конверт до {ENVELOPE_MAX_G} г).\n"
        f"⏱ Срок: ~{ETA_RANGE} рабочих дней.\n"
        "❌ Не принимаем: паспорта, ID-карты, военные билеты.\n\n"
        "Нажмите «Рассчитать точно» — 2 коротких вопроса кнопками, "
        "контакты понадобятся только для оформления."
    )


def step_question_answer() -> str:
    return (
        f"Коротко: от €{PRICE_ORDINARY_EUR} за конверт до {ENVELOPE_MAX_G} г, "
        f"срок ~{ETA_RANGE} рабочих дней; "
        "больше 0,5 кг — по согласованию. Паспорта, ID-карты и военные билеты не принимаем."
    )

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
    urgency = "обычная"  # single tariff; no urgent option

    price = base_price_eur(w, urgency) if w > 0 else None
    eta = eta_working_days(fc, tc)

    if w <= 0:
        notes = "укажите вес или число листов"
    elif w > ENVELOPE_MAX_G:
        notes = "вес > 0,5 кг — стоимость по согласованию"
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
        return TRACK_STATUSES.index(normalize_status(status) or status)
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


def normalize_phone(s: Optional[str]) -> str:
    """Strip spaces/brackets/dashes/dots and fix common local formats.

    0XXXXXXXXX (UA, 10 digits) -> +380XXXXXXXXX; 380…/375…/7… without + -> +…;
    8XXXXXXXXXX (RU, 11 digits) -> +7XXXXXXXXXX; 00… -> +…; other +… kept as is.
    """
    s = re.sub(r"[\s\-().]", "", (s or "").strip())
    if s.startswith("+"):
        return s
    if s.startswith("00"):
        return "+" + s[2:]
    if re.fullmatch(r"0\d{9}", s):
        return "+38" + s
    if re.fullmatch(r"8\d{10}", s):
        return "+7" + s[1:]
    if re.fullmatch(r"380\d{9}", s) or re.fullmatch(r"375\d{9}", s) or re.fullmatch(r"7\d{10}", s):
        return "+" + s
    return s


def valid_phone(s: str) -> bool:
    """International E.164-like: + and 8–15 digits (any country)."""
    return bool(re.fullmatch(r"\+[1-9]\d{7,14}", normalize_phone(s)))


NAME_LETTERS = "A-Za-zА-Яа-яЁёІіЇїЄєҐґ"
NAME_RE = re.compile(r"^[" + NAME_LETTERS + r"'’ʼ`.\-\s]+$")


def valid_name(s: str) -> bool:
    """Letters (RU/UA/Latin incl. і ї є ґ), apostrophes ' ’ ʼ, dot, space, hyphen; ≥ 2 letters."""
    s = (s or "").strip()
    if not s or not NAME_RE.match(s):
        return False
    return len(re.findall(r"[" + NAME_LETTERS + r"]", s)) >= 2


def mask_phone(s: Optional[str]) -> str:
    """'+380501234567' -> '+380…67' (for logs)."""
    d = re.sub(r"[^\d+]", "", s or "")
    if len(d) <= 6:
        return "***"
    return d[:4] + "…" + d[-2:]


def mask_name(s: Optional[str]) -> str:
    s = (s or "").strip()
    return (s[0] + "***") if s else ""


def mask_for_log(state: Optional[str], text: Optional[str]) -> str:
    """Hide personal data typed on name/phone steps; keep other answers readable."""
    t = text or ""
    if state == "ask_phone" or re.fullmatch(r"\+?[\d\s\-()]{7,}", t.strip()):
        return mask_phone(t)
    if state == "ask_name":
        return mask_name(t)
    return t[:80]


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


# ------------ Local time for people (Kyiv); Sheets stay in UTC ------------

DISPLAY_TZ_NAME = "Europe/Kyiv"
DISPLAY_TZ_LABEL = "Киев"


def _display_tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(DISPLAY_TZ_NAME)
    except Exception:            # tzdata missing: Europe/Kiev is the legacy alias
        return ZoneInfo("Europe/Kiev")


def to_local(dt):
    """Aware datetime → Kyiv time. Naive values are returned unchanged (unknown zone)."""
    if dt is None or getattr(dt, "tzinfo", None) is None:
        return dt
    return dt.astimezone(_display_tz())


def fmt_local(dt, fmt: str = "%d.%m.%Y %H:%M", label: bool = False) -> str:
    """Kyiv-time string for /track, /order, notifications; '' for None."""
    if not dt:
        return ""
    try:
        s = to_local(dt).strftime(fmt)
    except Exception:
        return str(dt)
    return f"{s} ({DISPLAY_TZ_LABEL})" if label else s
