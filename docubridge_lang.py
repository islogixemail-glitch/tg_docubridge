# -*- coding: utf-8 -*-
"""Reply language per chat: 'uk' or 'ru' (default).

Detection (simple, predictable): the client's last TEXT message — Ukrainian letters
і ї є ґ or a frequent Ukrainian word → 'uk'; any other letters → 'ru'. No text yet
(only button presses) → Telegram language_code ('uk…' → 'uk'). Stored in memory per
chat (and on the order payload as 'lang'); after a restart the language_code is the fallback.
"""
import re
import threading
from typing import Dict, Optional

LANGS = ("ru", "uk")
_UK_LETTERS = re.compile(r"[іїєґІЇЄҐ]")
_UK_WORDS = {
    "що", "як", "це", "чи", "або", "мені", "дякую", "ласка", "привіт",
    "треба", "потрібно", "скільки", "вартість", "оплатити", "надіслати", "можна",
    "яка", "який", "яке", "які", "адреса", "адресу", "знижка", "знижку", "знижки", "коштує",
    "дешевше", "людина", "людину", "потрібна", "потрібен",
}
_WORD = re.compile(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ'’ʼ]+")

_lock = threading.Lock()
_by_chat: Dict[int, str] = {}


def detect_lang(text: Optional[str]) -> Optional[str]:
    s = (text or "").strip()
    if not s or s.startswith("/"):
        return None
    if _UK_LETTERS.search(s):
        return "uk"
    words = [w.lower() for w in _WORD.findall(s)]
    if not words:
        return None
    return "uk" if any(w in _UK_WORDS for w in words) else "ru"


def from_user_code(user) -> Optional[str]:
    code = (getattr(user, "language_code", None) or "").lower()
    if not code:
        return None
    return "uk" if code.startswith("uk") else "ru"


def remember(chat_id: int, text: Optional[str]) -> Optional[str]:
    lang = detect_lang(text)
    if lang:
        with _lock:
            _by_chat[int(chat_id)] = lang
    return lang


def get_lang(chat_id: Optional[int] = None, user=None, default: str = "ru") -> str:
    if chat_id is not None:
        with _lock:
            lang = _by_chat.get(int(chat_id))
        if lang:
            return lang
    return from_user_code(user) or default


def norm(lang: Optional[str]) -> str:
    return lang if lang in LANGS else "ru"


def reset():  # tests
    with _lock:
        _by_chat.clear()
