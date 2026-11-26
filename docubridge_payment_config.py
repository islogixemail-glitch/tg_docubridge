# -*- coding: utf-8 -*-
"""Payment configuration approved by the owner. Logic never hard-codes these values.

Env overrides (the owner sets them; Render is not changed by code):
  DOCUBRIDGE_PAY_CURRENCIES      — currency buttons after the order, default «UAH,EUR,RUB» (not approved yet)
  DOCUBRIDGE_PAYMENT_TEXT_<CUR>  — payment details for one currency, shown verbatim
                                   («\\n» in the value = new line). Empty → the default below.

  DOCUBRIDGE_HANDOVER_TEXT_UA / _RUBY       — handover instructions by origin (RU text);
  DOCUBRIDGE_HANDOVER_TEXT_UA_UK / _RUBY_UK — the same in Ukrainian (else the base env, else default)

Approved by the owner 26.11.2026:
  * UAH details (verbatim, the payment purpose has NO ticket number), sent to the client ONLY
    after the documents arrived (admin /payreq or «📥 Документы получены»); other currencies: none yet →
    «реквизиты пришлёт менеджер» + a note to the leads group. No UAH amount / rate.
  * 100% prepayment once the documents have arrived: from Ukraine — in Bratislava,
    from Russia / Belarus — at our agent in Moscow (origin = the order's from_country).
  * Handover: from Ukraine the sender ships the documents on their own and at their own cost
    to our person in Slovakia (Nova Poshta or another convenient way); from Russia / Belarus —
    to Moscow (e.g. CDEK). The bot never gives addresses: the manager sends one after the
    client accepts the terms.
  * A human answers within 24 hours.
Not decided (placeholders): price over 500 g (discussed with the manager), UA funnel texts.
"""
import os
import re
from typing import Dict, List, Optional

DEFAULT_CURRENCIES = "UAH,EUR,RUB"

# Approved text — do not edit without the owner.
PAYMENT_TEXT_DEFAULTS: Dict[str, str] = {
    "UAH": (
        "Отримувач: ФОП Саєнко Ігор\n"
        "IBAN: UA853220010000026002310087318\n"
        "ІПН/ЄДРПОУ: 2468719172\n"
        "Банк: Акціонерне товариство УНІВЕРСАЛ БАНК, МФО 322001, ЄДРПОУ банку 21133352\n"
        "Призначення платежу: за послуги з організації доставки"
    ),
}

PREPAYMENT_PERCENT = 100

# Origin of the order (from_country) → group of rules.
ORIGIN_BY_COUNTRY = {"Украина": "UA", "Россия": "RUBY", "Беларусь": "RUBY"}

# Where the documents must arrive before payment («документы поступили …»).
ARRIVAL = {
    "UA": {"ru": "в Братиславу", "uk": "до Братислави"},
    "RUBY": {"ru": "нашему агенту в Москве", "uk": "нашому агенту в Москві"},
}
ARRIVAL_ANY = {
    "ru": "(из Украины — в Братиславу, из России и Беларуси — нашему агенту в Москве)",
    "uk": "(з України — до Братислави, з Росії та Білорусі — нашому агенту в Москві)",
}
# Where the client ships the documents (short, for the terms list).
SHIP_TO = {
    "UA": {"ru": "в Словакию", "uk": "до Словаччини"},
    "RUBY": {"ru": "в Москву", "uk": "до Москви"},
}
HANDOVER_DEFAULTS = {
    "UA": {
        "ru": "отправьте документы своими силами и за свой счёт на адрес нашего человека "
              "в Словакии — Новой Почтой или другим удобным способом.",
        "uk": "надішліть документи власними силами та за свій рахунок на адресу нашої людини "
              "у Словаччині — Новою Поштою або іншим зручним способом.",
    },
    "RUBY": {
        "ru": "отправьте документы своими силами и за свой счёт в Москву — например, СДЭКом.",
        "uk": "надішліть документи власними силами та за свій рахунок до Москви — наприклад, СДЕКом.",
    },
}
HANDOVER_ANY = {
    "ru": "документы отправляются своими силами и за свой счёт: из Украины — нашему человеку "
          "в Словакии (Новой Почтой или другим удобным способом), из России и Беларуси — в Москву "
          "(например, СДЭКом).",
    "uk": "документи надсилаються власними силами та за свій рахунок: з України — нашій людині "
          "у Словаччині (Новою Поштою або іншим зручним способом), з Росії та Білорусі — до Москви "
          "(наприклад, СДЕКом).",
}
# For the operator's «📬 нужен адрес» card.
ADDRESS_TAG = {"UA": "UA→SK", "RUBY": "RU-BY→Москва"}
ADDRESS_PLACE = {"UA": "Словакия (наш человек)", "RUBY": "Москва (наш агент)"}

RESPONSE_TIME = {"ru": "в течение 24 часов", "uk": "протягом 24 годин"}

# ---- Open questions (placeholders; None = the bot says nothing / hands over to a human) ----
OVERWEIGHT_PRICE: Optional[str] = None     # > 500 g: price is discussed with the manager


def origin_of(from_country: Optional[str]) -> Optional[str]:
    return ORIGIN_BY_COUNTRY.get((from_country or "").strip())


def _lang(lang: Optional[str]) -> str:
    return lang if lang in ("ru", "uk") else "ru"


def arrival(origin: Optional[str], lang: Optional[str]) -> str:
    lang = _lang(lang)
    return ARRIVAL[origin][lang] if origin in ARRIVAL else ARRIVAL_ANY[lang]


def ship_to(origin: Optional[str], lang: Optional[str]) -> Optional[str]:
    return SHIP_TO.get(origin, {}).get(_lang(lang))


def handover_text(origin: Optional[str], lang: Optional[str]) -> str:
    lang = _lang(lang)
    if origin not in HANDOVER_DEFAULTS:
        return HANDOVER_ANY[lang]
    base = f"DOCUBRIDGE_HANDOVER_TEXT_{origin}"
    env = ((os.getenv(base + "_UK") if lang == "uk" else None) or os.getenv(base) or "").strip()
    return env.replace("\\n", "\n") if env else HANDOVER_DEFAULTS[origin][lang]

_CUR_RE = re.compile(r"^[A-Z]{3}$")


def currencies() -> List[str]:
    raw = (os.getenv("DOCUBRIDGE_PAY_CURRENCIES") or "").strip() or DEFAULT_CURRENCIES
    out = []
    for part in raw.replace(";", ",").split(","):
        c = part.strip().upper()
        if _CUR_RE.match(c) and c not in out:
            out.append(c)
    return out or DEFAULT_CURRENCIES.split(",")


def payment_text_for(currency: Optional[str]) -> Optional[str]:
    """Verbatim details for the currency, or None if there are none yet."""
    cur = (currency or "").strip().upper()
    if not _CUR_RE.match(cur):
        return None
    env = (os.getenv(f"DOCUBRIDGE_PAYMENT_TEXT_{cur}") or "").strip()
    if env:
        return env.replace("\\n", "\n")
    return PAYMENT_TEXT_DEFAULTS.get(cur)
