import os
import re
import json
import traceback
from typing import Optional, Dict, Tuple
import threading # <-- ДОБАВЛЕНО: для фоновой обработки

from flask import Flask, request
from dotenv import load_dotenv

# загрузим .env ДО чтения переменных
load_dotenv()

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, Update

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
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

# ------------ App/Bot/AI ------------
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
print(f"[OpenAI] client is {'ON' if client else 'OFF'}")

# ------------ DB Connection Pool ------------
connection_pool = None

def init_db_pool():
    """Инициализирует пул соединений с БД"""
    global connection_pool
    if not DB_URL:
        return
    try:
        connection_pool = pool.SimpleConnectionPool(
            minconn=1,      # минимум 1 соединение
            maxconn=10,     # максимум 10 соединений
            dsn=DB_URL
        )
        print("[DB] Connection pool created")
    except Exception as e:
        print(f"[DB] Pool creation error: {e}")

def get_conn():
    """Получает соединение из пула"""
    if not connection_pool:
        return psycopg2.connect(DB_URL) if DB_URL else None
    return connection_pool.getconn()

def return_conn(conn):
    """Возвращает соединение в пул"""
    if connection_pool and conn:
        connection_pool.putconn(conn)

def ensure_tables():
    if not DB_URL:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
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
        -- НОВАЯ ТАБЛИЦА для защиты от дублей:
        CREATE TABLE IF NOT EXISTS processed_updates (
          update_id BIGINT PRIMARY KEY,
          processed_at TIMESTAMPTZ DEFAULT NOW()
        );
        -- Индекс для автоочистки старых записей (старше 7 дней)
        CREATE INDEX IF NOT EXISTS idx_processed_updates_time 
          ON processed_updates(processed_at);
        """)
        conn.commit(); cur.close(); conn.close()
        print("[DB] ensure_tables OK")
    except Exception as e:
        print(f"[DB] ensure_tables error: {e}")
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()

def is_update_processed(update_id: int) -> bool:
    """Проверяет, было ли обновление уже обработано"""
    if not DB_URL:
        return False  # Без БД не можем проверить
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM processed_updates WHERE update_id = %s", 
            (update_id,)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        return exists
    except Exception as e:
        print(f"[DB] is_update_processed error: {e}")
        return False  # В случае ошибки пропускаем проверку
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()
            
def mark_update_processed(update_id: int):
    """Отмечает обновление как обработанное"""
    if not DB_URL:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO processed_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (update_id,)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] mark_update_processed error: {e}")
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()

def cleanup_old_updates():
    """Удаляет записи старше 7 дней из processed_updates"""
    if not DB_URL:
        return
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM processed_updates 
            WHERE processed_at < NOW() - INTERVAL '7 days'
        """)
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
    conn = None
    try:
        if DB_URL:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""INSERT INTO chat_history(chat_id,user_message,bot_reply)
                           VALUES(%s,%s,%s)""", (int(chat_id), user_text, bot_reply))
            conn.commit()
            cur.close()
    except Exception as e:
        print(f"[DB] save_message error: {e}")
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()
    
    # УДАЛИЛИ блок с отправкой уведомлений админу!
    # Уведомления будут только при завершении визарда (в notify_admin_lead)

def get_state(chat_id: int) -> Tuple[str, Dict]:
    try:
        if not DB_URL:
            return ("greeting", {})
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT state,data FROM user_state WHERE chat_id=%s", (int(chat_id),))
        row = cur.fetchone(); cur.close(); conn.close()
        return (row["state"], row["data"] or {}) if row else ("greeting", {})
    except Exception as e:
        print(f"[DB] get_state error: {e}")
        return ("greeting", {})
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()    

def set_state(chat_id: int, state: str, data: Optional[Dict]=None):
    try:
        if not DB_URL: return
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""
          INSERT INTO user_state (chat_id,state,data,updated_at)
          VALUES (%s,%s,%s,NOW())
          ON CONFLICT (chat_id) DO UPDATE
            SET state=EXCLUDED.state, data=COALESCE(EXCLUDED.data, user_state.data), updated_at=NOW()
        """, (int(chat_id), state, json.dumps(data or {})))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] set_state error: {e}")
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()
            
def update_data(chat_id: int, new_data: Dict):
    try:
        if not DB_URL: return
        conn = get_conn(); cur = conn.cursor()
        cur.execute("""
          UPDATE user_state SET data=%s, updated_at=NOW() WHERE chat_id=%s
        """, (json.dumps(new_data), int(chat_id)))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] update_data error: {e}")
    finally:
        if conn:
            return_conn(conn)  # Возвращаем в пул вместо close()
            
# ------------ OpenAI (только вне визарда) ------------
def ai_reply(text: str) -> str:
    if not client:
        return "Сейчас умные ответы временно недоступны. Опишите задачу — менеджер поможет."
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":"Ты вежливый логист-ассистент DocuBridge. Отвечай по делу и кратко, на русском."},
                {"role":"user","content":text}
            ],
            temperature=0.6, max_tokens=500, timeout=30
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"[OpenAI] error: {e}")
        return "Небольшая пауза на стороне ИИ. Попробуйте ещё раз."

# ------------ Тариф/ETA ------------
TARIFF_TABLE = [(50,60),(100,65),(500,85)]  # € по весовым порогам
def base_price(weight:int):
    for thr,price in TARIFF_TABLE:
        if weight<=thr: return price,thr
    return None,None

def compute_quote(d:Dict)->Dict:
    fc=(d.get("from_country","").title())
    tc=(d.get("to_country","").title())
    w=int(d.get("weight_grams") or 0)
    price,thr = base_price(w)
    if fc=="Украина" and tc=="Россия": eta="27–29 дней"
    elif fc=="Украина" and tc=="Беларусь": eta="21–23 дня"
    elif fc in {"Россия","Беларусь"} and tc=="Украина": eta="уточним при оформлении (ориентир: 21–29 дней)"
    else: eta="требует подтверждения маршрута"
    notes=None
    if fc in {"Россия","Беларусь"} and tc=="Украина" and thr==50:
        price=50; notes="спец-тариф для РФ/РБ → UA (до 50 г)"
    if price is None:
        return {"price_eur":None,"threshold_g":None,"eta_text":eta,"notes":"вес >500 г — по согласованию"}
    return {"price_eur":price,"threshold_g":thr,"eta_text":eta,"notes":notes}

def notify_admin_lead(chat_id:int, payload:Dict):
    if not ADMIN_CHAT_ID: return
    try:
        q=compute_quote(payload)
        price_line = f"Оценка: €{q['price_eur']} (до {q['threshold_g']} г)" if q["price_eur"] is not None else "Оценка: по согласованию (>500 г)"
        eta_line = f"Срок: {q['eta_text']}"
        note_line = f"Примечание: {q['notes']}" if q.get("notes") else None
        lines=[
            "🟢 *Новый лид (DocuBridge)*",
            f"Chat ID: `{chat_id}`","",
            f"Тип документа: {payload.get('doc_type','—')}",
            f"Маршрут: {payload.get('from_country')}/{payload.get('from_city')} → {payload.get('to_country')}/{payload.get('to_city')}",
            f"Листов A4: {payload.get('pages_a4',0)}, вес ≈ {payload.get('weight_grams',0)} г",
            f"Срочность: {payload.get('urgency','—')}",
            "", f"Имя: {payload.get('name','—')}",
            f"Телефон: {payload.get('phone','—')}",
            f"Email: {payload.get('email','—')}",
            f"Лучшее время связи: {payload.get('best_time','—')}",
            "", price_line, eta_line
        ]
        if note_line: lines.append(note_line)
        bot.send_message(ADMIN_CHAT_ID, "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        print(f"[ADMIN notify] lead notify error: {e}")

# ------------ Визард (детерминистичный) ------------
COUNTRY_CHOICES = ["Украина","Россия","Беларусь"]

FIELDS = [
    {"key":"doc_type","type":"text","q":"Какой тип документа? (например: доверенность, диплом, свидетельство)"},
    {"key":"from_country","type":"choice","choices":COUNTRY_CHOICES,"q":"Из какой страны отправляем? (Украина/Россия/Беларусь)"},
    {"key":"from_city","type":"text","q":"Из какого города отправляем?"},
    {"key":"to_country","type":"choice","choices":COUNTRY_CHOICES,"q":"В какую страну доставляем? (Украина/Россия/Беларусь)"},
    {"key":"to_city","type":"text","q":"В какой город доставляем?"},
    {"key":"pages_a4","type":"int","q":"Сколько листов A4? (число)"},
    {"key":"weight_grams","type":"int_opt","q":"Если знаете точный вес в граммах — укажите, иначе напишите «нет»"},
    {"key":"urgency","type":"choice","choices":["обычная","срочная"],"q":"Срочность: обычная или срочная?"},
    {"key":"name","type":"name","q":"Как к вам обращаться (имя/фамилия)?"},
    {"key":"phone","type":"phone","q":"Контактный телефон (+380 / +7 / +375):"},
    {"key":"email","type":"email","q":"Электронная почта:"},
    {"key":"best_time","type":"text","q":"Когда вам удобнее принимать звонок/сообщение?"}
]

RUS_NUMS = {
    "ноль":0,"один":1,"два":2,"три":3,"четыре":4,"пять":5,"шесть":6,"семь":7,"восемь":8,"девять":9,
    "десять":10,"одиннадцать":11,"двенадцать":12,"тринадцать":13,"четырнадцать":14,"пятнадцать":15,
    "шестнадцать":16,"семнадцать":17,"восемнадцать":18,"девятнадцать":19,
    "двадцать":20,"тридцать":30,"сорок":40,"пятьдесят":50,"шестьдесят":60,"семьдесят":70,"восемьдесят":80,"девяносто":90,"сто":100
}

def parse_int(text:str)->Optional[int]:
    if not text: return None
    s=text.strip().lower()
    m=re.search(r"\d+", s)
    if m:
        try: return int(m.group())
        except: pass
    tokens=re.findall(r"[а-яё]+", s)
    total=0; last=0; seen=False
    for t in tokens:
        if t in RUS_NUMS:
            seen=True; val=RUS_NUMS[t]
            if val>=20 and val%10==0: last=val
            else:
                if last: total+=last+val; last=0
                else: total+=val
    if seen:
        return total if total>0 else (last if last>0 else None)
    return None

def valid_email(s:str)->bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s.strip(), flags=re.I))

def valid_phone(s:str)->bool:
    s=s.strip().replace(" ","")
    return s.startswith("+380") or s.startswith("+7") or s.startswith("+375")

def valid_name(s:str)->bool:
    s=s.strip()
    return bool(re.match(r"^[A-Za-zА-Яа-яЁё\-'\s]{2,}$", s))

def ask(chat_id:int, idx:int, data:Dict):
    field=FIELDS[idx]
    q=field["q"]
    # для choice дадим подсказку
    if field["type"]=="choice":
        q+=f" [{', '.join(field['choices'])}]"
    # клавиатура для стран/срочности
    kb=None
    if field["type"]=="choice":
        kb=ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        row=[]
        for choice in field["choices"]:
            row.append(KeyboardButton(choice))
            if len(row)==3:
                kb.add(*row); row=[]
        if row: kb.add(*row)
    save_message(chat_id, None, q)
    bot.send_message(chat_id, q, reply_markup=kb if kb else None)

def handle_answer(chat_id:int, text:str):
    state, data = get_state(chat_id)
    if state!="collecting":
        # не в визарде — обычный ответ
        reply = ai_reply(text)
        save_message(chat_id, text, reply)
        bot.send_message(chat_id, reply, reply_markup=main_menu())
        return

    data = data or {}
    idx = int(data.get("_idx", 0))
    if idx<0 or idx>=len(FIELDS): idx=0
    field=FIELDS[idx]
    key=field["key"]; t=field["type"]
    val=None; err=None

    s = (text or "").strip()

    if t=="text":
        val=s if len(s)>=1 else None
        if not val: err="Пустое значение. Повторите, пожалуйста."
    elif t=="choice":
        if s.title() in field["choices"]:
            val=s.title()
        else:
            err=f"Пожалуйста, выберите из вариантов: {', '.join(field['choices'])}"
    elif t=="int":
        n=parse_int(s)
        if n and n>0: val=n
        else: err="Нужно число > 0. Пример: 10"
    elif t=="int_opt":
        if s.lower() in {"нет","не знаю","unknown","нету","-" }:
            val=0
        else:
            n=parse_int(s)
            if n is None or n<0:
                err="Укажите число (например: 120) или напишите «нет»"
            else:
                val=n
    elif t=="phone":
        if valid_phone(s): val=s
        else: err="Телефон должен начинаться с +380 / +7 / +375 без лишних символов."
    elif t=="email":
        if valid_email(s): val=s
        else: err="Похоже на неверный email. Пример: name@example.com"
    elif t=="name":
        if valid_name(s): val=s
        else: err="Введите имя/фамилию (буквы, пробелы и дефисы; не короче 2 символов)."

    if err:
        save_message(chat_id, text, err)
        bot.send_message(chat_id, err)
        ask(chat_id, idx, data)
        return

    # записываем ответ
    data[key]=val

    # авторасчёт веса, если задано pages_a4, а weight_grams=0/пусто
    if key=="pages_a4":
        pages=int(val or 0)
        if pages>0 and int(data.get("weight_grams") or 0)==0:
            data["weight_grams"]=int((pages*6+5)//6*6)

    # следующий шаг
    idx+=1
    if idx<len(FIELDS):
        data["_idx"]=idx
        update_data(chat_id, data)
        ask(chat_id, idx, data)
        return

    # анкета готова → сохраним лид, начислим цену/ETA
    try:
        if DB_URL:
            conn=get_conn(); cur=conn.cursor()
            cur.execute("INSERT INTO leads(chat_id,payload) VALUES(%s,%s)",
                        (int(chat_id), psycopg2.extras.Json(data)))
            conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] INSERT lead error: {e}")

    quote=compute_quote(data)
    price_line = f"Стоимость: €{quote['price_eur']} (до {quote['threshold_g']} г)" if quote["price_eur"] is not None else "Стоимость: по согласованию (>500 г)"
    eta_line = f"Срок доставки: {quote['eta_text']}"

    # уведомим администратора
    notify_admin_lead(chat_id, data)

    reply = (
        "✅ Спасибо! Все данные получены.\n"
        f"Маршрут: {data.get('from_city')}, {data.get('from_country')} → "
        f"{data.get('to_city')}, {data.get('to_country')}\n"
        f"Листов A4: {data.get('pages_a4')} (≈ {data.get('weight_grams')} г)\n"
        f"{price_line}\n{eta_line}\n\n"
        f"Связаться: {data.get('name')}, {data.get('phone')}, {data.get('email')} ({data.get('best_time')})\n\n"
        "Если всё верно — подтвердите. Если нужно что-то изменить — просто напишите."
    )
    save_message(chat_id, text, reply)
    bot.send_message(chat_id, reply, reply_markup=main_menu())
    set_state(chat_id, "completed")

# ------------ UI / Handlers ------------
def main_menu():
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("/consult"))
    kb.add(KeyboardButton("/reset"))
    kb.add(KeyboardButton("/news"))
    return kb

@bot.message_handler(commands=['start'])
def start(message):
    msg=("Добро пожаловать в IS-Logix DocuBridge! 🇸🇰📄\n"
        "Нажмите /consult чтобы начать расчёт и оформление заявки.")
    save_message(message.chat.id, "/start", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())

@bot.message_handler(commands=['consult'])
def consult(message):
    # начинаем визард с нулевого шага
    data={"_idx":0}
    set_state(message.chat.id, "collecting", data)
    ask(message.chat.id, 0, data)

@bot.message_handler(commands=['reset'])
def reset(message):
    set_state(message.chat.id, "greeting", {})
    msg="Сбросил сессию. Нажмите /consult чтобы начать заново."
    save_message(message.chat.id, "/reset", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())

@bot.message_handler(commands=['news'])
def news(message):
    msg=("Новости DocuBridge: https://t.me/DocuBridgeInfo\n"
        "Готов помочь с вашим кейсом — /consult.")
    save_message(message.chat.id, "/news", msg)
    bot.send_message(message.chat.id, msg, reply_markup=main_menu())

@bot.message_handler(commands=['ai'])
def ai_ping(message):
    reply = ai_reply("Ответь одним словом: OK")
    save_message(message.chat.id, "/ai", reply)
    bot.send_message(message.chat.id, f"AI: {reply}")

@bot.message_handler(func=lambda m: True)
def any_text(message):
    # либо шаг визарда, либо обычный ответ ИИ
    handle_answer(message.chat.id, message.text)

# ------------ Webhook ------------
@app.route("/", methods=["GET"])
def index():
    return "OK", 200

# Вспомогательная функция, которая запускает обработку в фоне
def process_update_async(data):
    try:
        update=Update.de_json(json.loads(data.decode("utf-8")))
        bot.process_new_updates([update])
    except Exception as e:
        print("[Webhook] Async error:", e); traceback.print_exc()

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    try:
        if request.headers.get("content-type") == "application/json":
            json_data = json.loads(request.get_data().decode("utf-8"))
            update = Update.de_json(json_data)
            
            # ЗАЩИТА ОТ ДУБЛЕЙ: проверяем update_id
            update_id = update.update_id
            if is_update_processed(update_id):
                print(f"[Webhook] Update {update_id} уже обработан, пропускаем")
                return "OK", 200
            
            # Отмечаем как обработанный ДО обработки (важно!)
            mark_update_processed(update_id)
            
            # Теперь обрабатываем
            bot.process_new_updates([update])
        else:
            print("[Webhook] Unsupported content-type")
    except Exception as e:
        print("[Webhook] error:", e)
        traceback.print_exc()
    return "OK", 200
        
def ensure_webhook():
    try:
        if not WEBHOOK_BASE:
            print("WARNING: WEBHOOK_BASE не задан — вебхук не выставлен")
            return
        url=f"{WEBHOOK_BASE}/webhook/{WEBHOOK_SECRET}"
        bot.remove_webhook()
        ok=bot.set_webhook(url=url, drop_pending_updates=True)
        print(f"Webhook set to: {url}" if ok else "ERROR: set_webhook returned False")
    except Exception as e:
        print(f"[Webhook] set error: {e}")

# ------------ Entrypoint ------------
init_db_pool()      # Создаем пул соединений
ensure_tables()
ensure_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
