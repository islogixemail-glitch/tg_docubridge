import os
import re
import json
import traceback
from typing import Optional, Dict, Tuple, Any

from flask import Flask, request
from dotenv import load_dotenv

# загрузим .env ДО чтения переменных
load_dotenv()

import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    Update,
)

import psycopg2
import psycopg2.extras
from psycopg2 import pool
from openai import OpenAI

# ------------ ENV ------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set")
    raise SystemExit(1)

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    print("WARNING: DATABASE_URL не задан — сохранение истории отключено")

WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
PORT = int(os.getenv("PORT", "5000"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))  # один ID, как на Render

# ------------ App/Bot/AI ------------
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
print(f"[OpenAI] client is {'ON' if client else 'OFF'}")
print(f"[ADMIN] Admin ID: {ADMIN_CHAT_ID or '— (не задан)'}")

# ------------ DB Connection Pool ------------
connection_pool = None

def init_db_pool():
    """Инициализирует пул соединений с БД"""
    global connection_pool
    if not DB_URL:
        return
    try:
        connection_pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DB_URL,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        print("[DB] Connection pool created")
    except Exception as e:
        print(f"[DB] Pool creation error: {e}")

def get_conn():
    """Получает соединение из пула с проверкой валидности"""
    if not DB_URL:
        return None

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if not connection_pool:
                return psycopg2.connect(
                    DB_URL,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )

            conn = connection_pool.getconn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                return conn
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as db_err:
                print(f"[DB] Dead connection detected: {db_err}")
                try:
                    connection_pool.putconn(conn, close=True)
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    continue
                raise
        except Exception as e:
            print(f"[DB] get_conn error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                print("[DB] All connection attempts failed")
                return None
    return None

def return_conn(conn):
    """Возвращает соединение в пул"""
    if not conn:
        return
    try:
        if connection_pool:
            connection_pool.putconn(conn)
        else:
            conn.close()
    except Exception as e:
        print(f"[DB] return_conn error: {e}")
        try:
            conn.close()
        except Exception:
            pass

def ensure_tables():
    """Создаёт нужные таблицы (если их нет)"""
    conn = None
    if not DB_URL:
        return
    try:
        conn = get_conn()
        if not conn:
            print("[DB] ensure_tables: Failed to get connection")
            return

        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
              id BIGSERIAL PRIMARY KEY,
              chat_id BIGINT NOT NULL,
              user_message TEXT,
              bot_reply TEXT,
              timestamp TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS user_state (
              chat_id BIGINT PRIMARY KEY,
              state TEXT NOT NULL DEFAULT 'greeting',
              data JSONB DEFAULT '{}'::jsonb,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS leads (
              id BIGSERIAL PRIMARY KEY,
              chat_id BIGINT NOT NULL,
              payload JSONB NOT NULL,
              created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS processed_updates (
              update_id BIGINT PRIMARY KEY,
              processed_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_processed_updates_time
              ON processed_updates (processed_at);

            CREATE INDEX IF NOT EXISTS chat_history_ts_idx
              ON chat_history (timestamp DESC);
            """
        )
        conn.commit()
        cur.close()
        print("[DB] ensure_tables OK")
    except Exception as e:
        print(f"[DB] ensure_tables error: {e}")
    finally:
        if conn:
            return_conn(conn)

def is_update_processed(update_id: int) -> bool:
    """Проверяет, было ли обновление уже обработано"""
    conn = None
    if not DB_URL:
        return False
    try:
        conn = get_conn()
        if not conn:
            return False

        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM processed_updates WHERE update_id = %s",
            (update_id,),
        )
        exists = cur.fetchone() is not None
        cur.close()
        return exists
    except Exception as e:
        print(f"[DB] is_update_processed error: {e}")
        return False
    finally:
        if conn:
            return_conn(conn)

def mark_update_processed(update_id: int):
    """Отмечает обновление как обработанное"""
    conn = None
    if not DB_URL:
        return
    try:
        conn = get_conn()
        if not conn:
            print("[DB] mark_update_processed: Failed to get connection")
            return

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO processed_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (update_id,),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] mark_update_processed error: {e}")
    finally:
        if conn:
            return_conn(conn)

def cleanup_old_updates():
    """Удаляет записи старше 7 дней из processed_updates"""
    conn = None
    if not DB_URL:
        return
    try:
        conn = get_conn()
        if not conn:
            print("[DB] cleanup_old_updates: Failed to get connection")
            return

        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM processed_updates
            WHERE processed_at < NOW() - INTERVAL '7 days'
            """
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        print(f"[DB] Cleaned up {deleted} old update records")
    except Exception as e:
        print(f"[DB] cleanup_old_updates error: {e}")
    finally:
        if conn:
            return_conn(conn)

def save_message(chat_id: int, user_text: Optional[str], bot_reply: Optional[str]):
    """Сохраняет сообщение пользователя/бота в историю"""
    conn = None
    try:
        if DB_URL:
            conn = get_conn()
            if not conn:
                print("[DB] save_message: Failed to get connection")
                return
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO chat_history (chat_id, user_message, bot_reply)
                VALUES (%s, %s, %s)
                """,
                (int(chat_id), user_text, bot_reply),
            )
            conn.commit()
            cur.close()
    except Exception as e:
        print(f"[DB] save_message error: {e}")
    finally:
        if conn:
            return_conn(conn)

def get_state(chat_id: int) -> Tuple[str, Dict]:
    conn = None
    try:
        if not DB_URL:
            return ("greeting", {})

        conn = get_conn()
        if not conn:
            return ("greeting", {})

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT state, data FROM user_state WHERE chat_id = %s", (int(chat_id),))
        row = cur.fetchone()
        cur.close()
        return (row["state"], row["data"] or {}) if row else ("greeting", {})
    except Exception as e:
        print(f"[DB] get_state error: {e}")
        return ("greeting", {})
    finally:
        if conn:
            return_conn(conn)

def set_state(chat_id: int, state: str, data: Optional[Dict] = None):
    conn = None
    try:
        if not DB_URL:
            return

        conn = get_conn()
        if not conn:
            print("[DB] set_state: Failed to get connection")
            return

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_state (chat_id, state, data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE
              SET state = EXCLUDED.state,
                  data  = COALESCE(EXCLUDED.data, user_state.data),
                  updated_at = NOW()
            """,
            (int(chat_id), state, json.dumps(data or {})),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] set_state error: {e}")
    finally:
        if conn:
            return_conn(conn)

def update_data(chat_id: int, new_data: Dict):
    conn = None
    try:
        if not DB_URL:
            return

        conn = get_conn()
        if not conn:
            print("[DB] update_data: Failed to get connection")
            return

        cur = conn.cursor()
        cur.execute(
            """
            UPDATE user_state
               SET data = %s, updated_at = NOW()
             WHERE chat_id = %s
            """,
            (json.dumps(new_data), int(chat_id)),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] update_data error: {e}")
    finally:
        if conn:
            return_conn(conn)

# ------------ OpenAI (общие ответы) ------------
def ai_reply(text: str) -> str:
    if not client:
        return "Сейчас умные ответы временно недоступны. Опишите задачу — менеджер поможет."
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты вежливый логист-ассистент DocuBridge. Отвечай по делу и кратко, на русском."},
                {"role": "user", "content": text},
            ],
            temperature=0.6,
            max_tokens=500,
            timeout=30,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"[OpenAI] error: {e}")
        return "Небольшая пауза на стороне ИИ. Попробуйте ещё раз."

# ------------ Тарифы (единые по всем направлениям) ------------
PRICING = {
    "обычная": [(50, 65), (100, 85)],   # ≤50г — €65; ≤100г — €85
    "срочная": [(50, 110), (100, 130)], # ≤50г — €110; ≤100г — €130
}

def base_price(weight: int, tariff_table):
    for thr, price in tariff_table:
        if weight <= thr:
            return price, thr
    return None, None

def eta_working_days(from_country: str, to_country: str) -> Optional[str]:
    """'База' ориентировочных сроков в РАБОЧИХ днях по маршрутам."""
    fc = (from_country or "").title()
    tc = (to_country or "").title()
    if fc == "Украина" and tc == "Россия":
        return "27–29"
    if fc == "Украина" and tc == "Беларусь":
        return "21–23"
    if fc in {"Россия", "Беларусь"} and tc == "Украина":
        return None  # требует подтверждения
    return None  # неизвестный маршрут

def compute_quote(d: Dict) -> Dict:
    fc = (d.get("from_country", "") or "").title()
    tc = (d.get("to_country", "") or "").title()
    w  = int(d.get("weight_grams") or 0)

    urgency = (d.get("urgency") or "обычная").strip().lower()
    if urgency not in PRICING:
        urgency = "обычная"

    price, thr = base_price(w, PRICING[urgency])
    eta_work = eta_working_days(fc, tc)

    if w == 0 or price is None:
        return {
            "price_eur": None,
            "threshold_g": None,
            "eta_working": eta_work,
            "notes": "вес 0 г или >100 г — стоимость по согласованию",
            "urgency": urgency,
        }

    notes = "ускоренная доставка" if urgency == "срочная" else None

    return {
        "price_eur": price,
        "threshold_g": thr,
        "eta_working": eta_work,   # строка вроде "27–29" или None
        "notes": notes,
        "urgency": urgency,
    }

# ------------ Уведомление админу (НЕ пользователю) ------------
def notify_admin_lead(source_chat_id: int, payload: Dict):
    """Отправляет карточку лида администратору. Пользователю НЕ показывается."""
    if not ADMIN_CHAT_ID:
        print("[ADMIN] ADMIN_CHAT_ID не задан — уведомление не отправлено")
        return
    if ADMIN_CHAT_ID == source_chat_id:
        print("[ADMIN] ADMIN_CHAT_ID совпадает с chat_id пользователя — уведомление пропущено (тестовый режим).")
        return
    try:
        q = compute_quote(payload)
        price_line = f"Оценка: €{q['price_eur']} (до {q['threshold_g']} г)" if q["price_eur"] is not None else "Оценка: по согласованию"
        eta_line = (
            f"Срок: ориентировочно {q['eta_working']} рабочих дней" if q.get("eta_working")
            else "Срок: требует подтверждения маршрута"
        )
        note_line = f"Примечание: {q['notes']}" if q.get("notes") else None
        lines = [
            "🟢 *Новый лид (DocuBridge)*",
            f"Chat ID: `{source_chat_id}`",
            "",
            f"Тип документа: {payload.get('doc_type', '—')}",
            f"Маршрут: {payload.get('from_country')}/{payload.get('from_city')} → {payload.get('to_country')}/{payload.get('to_city')}",
            f"Листов A4: {payload.get('pages_a4', 0)}, вес ≈ {payload.get('weight_grams', 0)} г",
            f"Срочность: {payload.get('urgency', '—')}",
            "",
            price_line,
            eta_line,
        ]
        if note_line:
            lines.append(note_line)
        lines += [
            "",
            f"Имя: {payload.get('name', '—')}",
            f"Телефон: {payload.get('phone', '—')}",
            f"Email: {payload.get('email', '—')}",
            f"Лучшее время связи: {payload.get('best_time', '—')}",
        ]
        bot.send_message(ADMIN_CHAT_ID, "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        print(f"[ADMIN notify] lead notify error: {e}")

# ------------ Визард ------------
COUNTRY_CHOICES = ["Украина", "Россия", "Беларусь"]

FIELDS = [
    {"key": "doc_type", "type": "text", "q": "Какой тип документа? (например: доверенность, диплом, свидетельство)"},
    {"key": "from_country", "type": "choice", "choices": COUNTRY_CHOICES, "q": "Из какой страны отправляем? (Украина/Россия/Беларусь)"},
    {"key": "from_city", "type": "text", "q": "Из какого города отправляем?"},
    {"key": "to_country", "type": "choice", "choices": COUNTRY_CHOICES, "q": "В какую страну доставляем? (Украина/Россия/Беларусь)"},
    {"key": "to_city", "type": "text", "q": "В какой город доставляем?"},
    {"key": "pages_a4", "type": "int", "q": "Сколько листов A4? (число)"},
    {"key": "weight_grams", "type": "int_opt", "q": "Если знаете точный вес в граммах — укажите, иначе напишите «нет»"},
    {"key": "urgency", "type": "choice", "choices": ["обычная", "срочная"], "q": "Срочность: обычная или срочная?"},
    {"key": "name", "type": "name", "q": "Как к вам обращаться (имя/фамилия)?"},
    {"key": "phone", "type": "phone", "q": "Контактный телефон (+380 / +7 / +375):"},
    {"key": "email", "type": "email", "q": "Электронная почта:"},
    {"key": "best_time", "type": "text", "q": "Когда вам удобнее принимать звонок/сообщение?"},
]

RUS_NUMS = {
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100
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

def valid_email(s: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s.strip(), flags=re.I))

def valid_phone(s: str) -> bool:
    s = s.strip().replace(" ", "")
    return s.startswith("+380") or s.startswith("+7") or s.startswith("+375")

def valid_name(s: str) -> bool:
    s = s.strip()
    return bool(re.match(r"^[A-Za-zА-Яа-яЁё\-'\s]{2,}$", s))

# ------------ ИИ + ЭВРИСТИКИ: распознавание намерений ------------
AI_KEYS = {"doc_type","from_country","from_city","to_country","to_city","pages_a4","weight_grams","urgency","name","phone","email","best_time"}

URGENCY_SYNONYMS = {
    "срочная": [
        "срочно","срочная","экспресс","быстро","быстрее",
        "как можно быстро","как можно быстрее","urgent","express","ускоренная","ускоренный","максимально быстро"
    ],
    "обычная": [
        "обычно","обычная","стандарт","стандартный","базовый","не быстрый","небыстро","normal","standard","без спешки"
    ],
}

def infer_urgency(text: str) -> Optional[str]:
    s = (text or "").lower()
    for label, words in URGENCY_SYNONYMS.items():
        for w in words:
            if w in s:
                return label
    return None

def heuristic_parse(text: str) -> Optional[Dict[str, Any]]:
    """Быстрый локальный парсер: вытаскивает срочность/страницы/вес без ИИ."""
    if not text:
        return None
    out: Dict[str, Any] = {}

    # срочность
    u = infer_urgency(text)
    if u:
        out["urgency"] = u

    # вес, г
    m = re.search(r"(\d+)\s*(?:г|гр|грамм)", text.lower())
    if m:
        try:
            out["weight_grams"] = int(m.group(1))
        except:
            pass

    # страницы
    m = re.search(r"(\d+)\s*(?:стр|лист)", text.lower())
    if m:
        try:
            out["pages_a4"] = int(m.group(1))
        except:
            pass

    # автоподстановка веса по страницам
    if "pages_a4" in out and "weight_grams" not in out:
        pages = int(out["pages_a4"] or 0)
        if pages > 0:
            out["weight_grams"] = pages * 6

    return out or None

def normalize_country(x: Optional[str]) -> Optional[str]:
    if not x: return None
    s = x.strip().lower()
    mapping = {
        "украина":"Украина","ukraine":"Украина","ua":"Украина",
        "россия":"Россия","rf":"Россия","ru":"Россия","russia":"Россия",
        "беларусь":"Беларусь","рб":"Беларусь","by":"Беларусь","belarus":"Беларусь",
    }
    return mapping.get(s, x.strip().title())

def normalize_urgency(x: Optional[str]) -> Optional[str]:
    if not x: return None
    s = x.strip().lower()
    if s in {"обычная","standard","normal","базовый","стандартный"}: return "обычная"
    if s in {"срочная","express","urgent","ускоренная","ускоренный","экспресс"}: return "срочная"
    return None

def ai_understand(text: str) -> Optional[Dict[str, Any]]:
    """Пытается извлечь JSON с полями анкеты из свободного текста пользователя."""
    if not client:
        return None
    try:
        system = (
            "Ты логистический ассистент DocuBridge. "
            "Тебе дают свободный текст. Извлеки поля заявки "
            "(doc_type, from_country, from_city, to_country, to_city, pages_a4, weight_grams, urgency, name, phone, email, best_time). "
            "Верни ТОЛЬКО валидный JSON-объект без лишнего текста. Неуказанные поля не включай."
        )
        user = "Текст пользователя:\n" + text
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.2,
            max_tokens=400,
            timeout=30
        )
        raw = (r.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return None

        cleaned: Dict[str, Any] = {}
        for k, v in data.items():
            if k not in AI_KEYS or v is None:
                continue
            if k in {"pages_a4","weight_grams"}:
                try:
                    iv = int(v)
                    if iv >= 0:
                        cleaned[k] = iv
                except Exception:
                    pass
            elif k in {"from_country","to_country"}:
                nv = normalize_country(str(v))
                if nv:
                    cleaned[k] = nv
            elif k == "urgency":
                nu = normalize_urgency(str(v))
                if nu:
                    cleaned[k] = nu
            else:
                sv = str(v).strip()
                if sv:
                    cleaned[k] = sv

        if "phone" in cleaned and not valid_phone(cleaned["phone"]):
            cleaned.pop("phone", None)
        if "email" in cleaned and not valid_email(cleaned["email"]):
            cleaned.pop("email", None)
        if "name" in cleaned and not valid_name(cleaned["name"]):
            cleaned.pop("name", None)

        if "pages_a4" in cleaned and ("weight_grams" not in cleaned or cleaned.get("weight_grams",0) == 0):
            pages = int(cleaned["pages_a4"] or 0)
            if pages > 0:
                cleaned["weight_grams"] = pages * 6

        return cleaned if cleaned else None
    except Exception as e:
        print(f"[OpenAI] ai_understand error: {e}")
        return None

def first_missing_index(data: Dict) -> int:
    """Возвращает индекс первого незаполненного поля по FIELDS; если всё заполнено — len(FIELDS)."""
    def is_filled(field, value) -> bool:
        t = field["type"]
        if value is None:
            return False
        s = str(value).strip() if not isinstance(value, int) else value
        if t == "text":
            return bool(s) and len(str(s)) >= 1
        if t == "choice":
            return str(value).strip() in field["choices"]
        if t == "int":
            try:
                return int(value) > 0
            except Exception:
                return False
        if t == "int_opt":
            try:
                iv = int(value)
                return iv >= 0
            except Exception:
                return False
        if t == "phone":
            return valid_phone(str(value))
        if t == "email":
            return valid_email(str(value))
        if t == "name":
            return valid_name(str(value))
        return False

    for i, f in enumerate(FIELDS):
        k = f["key"]
        if not is_filled(f, data.get(k)):
            return i
    return len(FIELDS)

def merge_ai_data(existing: Dict, parsed: Dict) -> Dict:
    """Мержит распознанные поля в data, не стирая уже заполненные значения."""
    merged = dict(existing or {})
    for k in AI_KEYS:
        if k in parsed and (merged.get(k) in (None, "", 0) or k not in merged):
            merged[k] = parsed[k]
    if merged.get("pages_a4") and not merged.get("weight_grams"):
        try:
            pages = int(merged["pages_a4"])
            if pages > 0:
                merged["weight_grams"] = pages * 6
        except Exception:
            pass
    return merged

# --- АЛИАСЫ ПОЛЕЙ ДЛЯ КОМАНД «ИЗМЕНИ/ВЕРНИ К ...» ---
FIELD_ALIASES = {
    "doc_type":      ["тип документа", "документ", "вид документа"],
    "from_country":  ["страна отправки", "страна откуда", "из страны", "страна-отправитель"],
    "from_city":     ["город отправки", "город откуда", "из города"],
    "to_country":    ["страна доставки", "страна назначения", "в страну", "страна-получатель"],
    "to_city":       ["город доставки", "город назначения", "в город"],
    "pages_a4":      ["страницы", "страниц", "листов", "листы", "количество страниц", "кол-во листов", "а4"],
    "weight_grams":  ["вес", "масса", "грамм", "граммы"],
    "urgency":       ["срочность", "скорость", "режим доставки"],
    "name":          ["имя", "фамилия", "как обращаться"],
    "phone":         ["телефон", "номер", "контакт"],
    "email":         ["почта", "email", "e-mail", "электронная почта"],
    "best_time":     ["время связи", "когда связаться", "лучшее время"],
}

def alias_to_key(text: str) -> Optional[str]:
    s = (text or "").lower()
    for key, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if a in s:
                return key
    return None

def try_extract_value_for_key(key: str, text: str) -> Optional[Any]:
    s = (text or "").strip()

    if key == "urgency":
        syn = infer_urgency(s)
        if syn:
            return syn

    if key in {"pages_a4", "weight_grams"}:
        m = re.search(r"(\d+)", s.lower())
        if m:
            try:
                val = int(m.group(1))
                if key == "pages_a4" and val >= 0:
                    return val
                if key == "weight_grams" and val >= 0:
                    return val
            except:
                pass
        return None

    if key in {"from_country", "to_country"}:
        m = re.search(r"(?:из|в|во)\s+([A-Za-zА-Яа-яЁё\-]+)", s, flags=re.I)
        cand = m.group(1) if m else s
        return normalize_country(cand)

    if key in {"from_city", "to_city"}:
        m = re.search(r"(?:город|в|из)\s+([A-Za-zА-Яа-яЁё\-\s]{2,})", s, flags=re.I)
        return (m.group(1).strip().title() if m else None)

    if key == "phone":
        m = re.search(r"(\+\d[\d\s\-]{6,})", s)
        if m:
            cand = m.group(1).replace(" ", "")
            return cand if valid_phone(cand) else None

    if key == "email":
        m = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", s)
        if m:
            return m.group(1) if valid_email(m.group(1)) else None

    if key == "name":
        m = re.search(r"(?:меня зовут|мо[её] имя|я\s*[-—]\s*)(.+)", s, flags=re.I)
        if m:
            cand = m.group(1).strip()
            return cand if valid_name(cand) else None

    if key == "best_time":
        m = re.search(r"(после|до|в)\s+[^,.!?]+", s, flags=re.I)
        return m.group(0) if m else s

    if key == "doc_type":
        return s if len(s) >= 2 else None

    return None

def detect_jump_or_edit(text: str) -> Tuple[Optional[str], Optional[Any]]:
    """Возвращает (key, new_value) для команд: верни/исправь/поменяй ... [на <значение>]"""
    s = (text or "").lower()
    if not s:
        return (None, None)
    if any(w in s for w in ["верни", "вернуть", "вернись", "исправ", "поправ", "измен", "поменя", "коррект"]):
        key = alias_to_key(s)
        if not key:
            return (None, None)
        new_val = try_extract_value_for_key(key, text)
        return (key, new_val)
    return (None, None)

# ------------ UI / Диалог ------------
def ask(chat_id: int, idx: int, data: Dict):
    field = FIELDS[idx]
    q = field["q"]

    if field["type"] == "choice":
        q += f" [{', '.join(field['choices'])}]"

    kb = None
    if field["type"] == "choice":
        kb = ReplyKeyboardMarkup(
            resize_keyboard=True,
            one_time_keyboard=True,
            input_field_placeholder="Выберите вариант на клавиатуре ниже"
        )
        row = []
        for choice in field["choices"]:
            row.append(KeyboardButton(choice))
            if len(row) == 3:
                kb.add(*row)
                row = []
        if row:
            kb.add(*row)

    save_message(chat_id, None, q)
    bot.send_message(chat_id, q, reply_markup=kb if kb else None)

def handle_answer(chat_id: int, text: str):
    print(f"[Handler] handle_answer called: chat_id={chat_id}, text='{text}'")

    state, data = get_state(chat_id)
    save_message(chat_id, text, None)

    # 🔹 Команды "верни/исправь": переход на нужный шаг, опционально сразу применяем новое значение
    jump_key, new_val = detect_jump_or_edit(text)
    if jump_key:
        data = (data or {})
        if new_val is not None:
            data[jump_key] = new_val
            if jump_key == "pages_a4" and int(data.get("weight_grams") or 0) == 0:
                try:
                    pages = int(new_val)
                    if pages > 0:
                        data["weight_grams"] = pages * 6
                except:
                    pass
            update_data(chat_id, data)
            idx = first_missing_index(data)
            if idx >= len(FIELDS):
                return finalize_form(chat_id, data, last_user_text=text)
        else:
            idx = next((i for i, f in enumerate(FIELDS) if f["key"] == jump_key), 0)

        data["_idx"] = idx
        set_state(chat_id, "collecting", data)
        bot.send_message(chat_id, "Ок, вернул к запрошенному шагу. Уточните, пожалуйста.", reply_markup=ReplyKeyboardRemove())
        ask(chat_id, idx, data)
        return

    # ВНЕ визарда: сначала эвристика, потом ИИ
    if state != "collecting":
        parsed = heuristic_parse(text) or ai_understand(text)
        if parsed:
            print(f"[AI] Parsed intent: {parsed}")
            data = merge_ai_data({}, parsed)
            idx = first_missing_index(data)
            if idx >= len(FIELDS):
                return finalize_form(chat_id, data, last_user_text=text)
            else:
                data["_idx"] = idx
                set_state(chat_id, "collecting", data)
                bot.send_message(chat_id, "Понял вас. Давайте уточним пару моментов.", reply_markup=ReplyKeyboardRemove())
                ask(chat_id, idx, data)
                return

        reply = ai_reply(text)
        save_message(chat_id, text, reply)
        bot.send_message(chat_id, reply, reply_markup=main_menu())
        return

    # В ВИЗАРДЕ: пробуем распознать текст, но применяем ТОЛЬКО если данные реально изменились
    data = data or {}
    ai_try = heuristic_parse(text) or ai_understand(text)
    if ai_try:
        before = dict(data)
        merged = merge_ai_data(data, ai_try)
        if merged != before:
            print(f"[AI] In-wizard parsed & applied: {ai_try}")
            data = merged
            idx = first_missing_index(data)
            if idx >= len(FIELDS):
                return finalize_form(chat_id, data, last_user_text=text)
            else:
                data["_idx"] = idx
                update_data(chat_id, data)
                bot.send_message(chat_id, "Принято. Продолжим.", reply_markup=ReplyKeyboardRemove())
                ask(chat_id, idx, data)
                return
        # иначе ИИ ничего полезного не добавил — идём на обычную валидацию

    # обычная пошаговая валидация
    idx = int(data.get("_idx", 0))
    if idx < 0 or idx >= len(FIELDS):
        idx = 0
    field = FIELDS[idx]
    key = field["key"]
    t = field["type"]
    val = None
    err = None
    s = (text or "").strip()

    if t == "text":
        val = s if len(s) >= 1 else None
        if not val:
            err = "Пустое значение. Повторите, пожалуйста."
    elif t == "choice":
        norm_map = {str(c).lower(): c for c in field["choices"]}
        s_norm = s.lower()
        if key == "urgency":
            syn = infer_urgency(s)
            if syn:
                s_norm = syn
        if s_norm in norm_map:
            val = norm_map[s_norm]
            print(f"[Handler] Choice accepted: '{s}' -> '{val}'")
        else:
            err = f"Пожалуйста, выберите из вариантов: {', '.join(field['choices'])}"
    elif t == "int":
        n = parse_int(s)
        if n and n > 0:
            val = n
        else:
            err = "Нужно число > 0. Пример: 10"
    elif t == "int_opt":
        if s.lower() in {"нет", "не знаю", "unknown", "нету", "-"}:
            val = 0
        else:
            n = parse_int(s)
            if n is None or n < 0:
                err = "Укажите число (например: 120) или напишите «нет»"
            else:
                val = n
    elif t == "phone":
        if valid_phone(s):
            val = s
        else:
            err = "Телефон должен начинаться с +380 / +7 / +375 без лишних символов."
    elif t == "email":
        if valid_email(s):
            val = s
        else:
            err = "Похоже на неверный email. Пример: name@example.com"
    elif t == "name":
        if valid_name(s):
            val = s
        else:
            err = "Введите имя/фамилию (буквы, пробелы и дефисы; не короче 2 символов)."

    if err:
        save_message(chat_id, None, err)
        bot.send_message(chat_id, err)
        ask(chat_id, idx, data)
        return

    data[key] = val

    if key == "pages_a4":
        pages = int(val or 0)
        if pages > 0 and int(data.get("weight_grams") or 0) == 0:
            data["weight_grams"] = pages * 6

    idx += 1
    if idx < len(FIELDS):
        data["_idx"] = idx
        update_data(chat_id, data)
        bot.send_message(chat_id, "Принято.", reply_markup=ReplyKeyboardRemove())
        ask(chat_id, idx, data)
        return

    finalize_form(chat_id, data, last_user_text=text)

def finalize_form(chat_id: int, data: Dict, last_user_text: Optional[str] = None):
    conn = None
    try:
        if DB_URL:
            conn = get_conn()
            if not conn:
                print("[DB] Failed to save lead: no connection")
            else:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO leads(chat_id,payload) VALUES(%s,%s)",
                    (int(chat_id), psycopg2.extras.Json(data)),
                )
                conn.commit()
                cur.close()
    except Exception as e:
        print(f"[DB] INSERT lead error: {e}")
    finally:
        if conn:
            return_conn(conn)

    quote = compute_quote(data)
    price_line = (
        f"Стоимость: €{quote['price_eur']} (до {quote['threshold_g']} г)"
        if quote["price_eur"] is not None else
        "Стоимость: по согласованию"
    )

    # строка срока
    if quote.get("urgency") == "срочная":
        eta_line = "Срок доставки: ускоренная доставка"
    else:
        eta_line = (
            f"Срок доставки: ориентировочно {quote['eta_working']} рабочих дней"
            if quote.get("eta_working")
            else "Срок доставки: требует подтверждения маршрута"
        )

    notes_line = f"{quote['notes']}" if quote.get("notes") else None

    # Уведомляем только админа (не пользователя)
    notify_admin_lead(chat_id, data)

    reply = (
        "✅ Спасибо! Все данные получены.\n"
        f"Маршрут: {data.get('from_city')}, {data.get('from_country')} → "
        f"{data.get('to_city')}, {data.get('to_country')}\n"
        f"Листов A4: {data.get('pages_a4')} (≈ {data.get('weight_grams')} г)\n"
        f"{price_line}\n{eta_line}\n"
        + (f"{notes_line}\n\n" if notes_line else "\n") +
        f"Связаться: {data.get('name')}, {data.get('phone')}, {data.get('email')} ({data.get('best_time')})\n\n"
        "Если всё верно — просто ожидайте ответ нашего специалиста. Если нужно что-то изменить — пройдите опрос снова."
    )
    save_message(chat_id, last_user_text or "", reply)
    bot.send_message(chat_id, reply, reply_markup=main_menu())
    set_state(chat_id, "completed")

# ------------ UI / Handlers ------------
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("/consult"))
    kb.add(KeyboardButton("/reset"))
    kb.add(KeyboardButton("/news"))
    return kb

@bot.message_handler(commands=['start'])
def start(message):
    msg = (
        "Добро пожаловать в IS-Logix DocuBridge! 🇸🇰📄\n"
        "Нажмите /consult чтобы начать расчёт и оформление заявки.\n"
        "Либо опишите задачу свободным текстом — я постараюсь понять и заполнить анкету автоматически."
    )
    save_message(message.chat.id, "/start", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())

@bot.message_handler(commands=['consult'])
def consult(message):
    data = {"_idx": 0}
    set_state(message.chat.id, "collecting", data)
    ask(message.chat.id, 0, data)

@bot.message_handler(commands=['reset'])
def reset(message):
    set_state(message.chat.id, "greeting", {})
    msg = "Сбросил сессию. Нажмите /consult чтобы начать заново."
    save_message(message.chat.id, "/reset", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())

@bot.message_handler(commands=['news'])
def news(message):
    msg = (
        "Новости DocuBridge: https://t.me/DocuBridgeInfo\n"
        "Готов помочь с вашим кейсом — /consult."
    )
    save_message(message.chat.id, "/news", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())

@bot.message_handler(commands=['ai'])
def ai_ping(message):
    reply = ai_reply("Ответь одним словом: OK")
    save_message(message.chat.id, "/ai", reply)
    bot.send_message(message.chat.id, f"AI: {reply}")

@bot.message_handler(func=lambda m: True)
def any_text(message):
    handle_answer(message.chat.id, message.text)

# ------------ Webhook ------------
@app.route("/", methods=["GET"])
def index():
    return "OK", 200

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    try:
        if request.headers.get("content-type") == "application/json":
            json_data = json.loads(request.get_data().decode("utf-8"))
            update = Update.de_json(json_data)

            update_id = update.update_id
            print(f"[Webhook] Received update_id: {update_id}")

            if is_update_processed(update_id):
                print(f"[Webhook] Update {update_id} уже обработан, пропускаем")
                return "OK", 200

            mark_update_processed(update_id)
            print(f"[Webhook] Processing update_id: {update_id}")

            bot.process_new_updates([update])
            print(f"[Webhook] Update {update_id} processed successfully")
        else:
            print("[Webhook] Unsupported content-type")
    except Exception as e:
        print("[Webhook] error:", e)
        traceback.print_exc()
    return "OK", 200

def ensure_webhook():
    try:
        if not WEBHOOK_BASE:
            print("❌ ERROR: WEBHOOK_BASE не задан — бот не будет работать!")
            print("Установите WEBHOOK_BASE в .env файле")
            raise SystemExit(1)

        url = f"{WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}"
        bot.remove_webhook()
        ok = bot.set_webhook(url=url, drop_pending_updates=True)
        if ok:
            print(f"✅ Webhook set to: {url}")
        else:
            print("❌ ERROR: set_webhook returned False")
            raise SystemExit(1)
    except Exception as e:
        print(f"❌ [Webhook] set error: {e}")
        raise SystemExit(1)

# ------------ Entrypoint ------------
init_db_pool()
ensure_tables()
ensure_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
