# -*- coding: utf-8 -*-
"""Google Sheet «Заявки DocuBridge» export (best-effort, non-blocking).

Env: GOOGLE_SA_JSON (service-account JSON content), SHEET_ID.
Any missing env or error is logged and skipped — never breaks an order.
"""
import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

SHEET_HEADER = [
    "Ticket", "Дата (UTC)", "Chat ID", "Роль", "Откуда", "Куда",
    "Тип документа", "Листов", "Вес, г", "Срочность", "Оценка, €",
    "Имя", "Телефон", "Источник", "Статус", "Обновлено (UTC)", "Комментарий",
]
STATUS_COL = SHEET_HEADER.index("Статус") + 1
UPDATED_COL = SHEET_HEADER.index("Обновлено (UTC)") + 1
COMMENT_COL = SHEET_HEADER.index("Комментарий") + 1

_ws_cache = None
_lock = threading.Lock()


def sheets_enabled() -> bool:
    return bool(os.getenv("GOOGLE_SA_JSON", "").strip() and os.getenv("SHEET_ID", "").strip())


def _get_worksheet():
    global _ws_cache
    if _ws_cache is not None:
        return _ws_cache
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(os.getenv("GOOGLE_SA_JSON"))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(os.getenv("SHEET_ID").strip()).sheet1
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADER, value_input_option="USER_ENTERED")
    _ws_cache = ws
    return ws


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def build_row(chat_id: int, payload: Dict, price_eur: Optional[int] = None) -> list:
    p = payload or {}
    return [
        p.get("ticket", ""), _now(), str(chat_id), p.get("role", ""),
        p.get("from_country", ""), p.get("to_country", ""), p.get("doc_type", ""),
        p.get("pages_a4", ""), p.get("weight_grams", ""), p.get("urgency", ""),
        "" if price_eur is None else price_eur, p.get("name", ""),
        # leading ' keeps "+380…" as text
        ("'" + p["phone"]) if p.get("phone") else "",
        p.get("ref") or p.get("start_payload") or "", "принято", _now(), "",
    ]


def _append(row: list):
    try:
        with _lock:
            _get_worksheet().append_row(row, value_input_option="USER_ENTERED")
        print(f"[Sheets] appended {row[0]}")
    except Exception as e:
        print(f"[Sheets] append skipped: {e}")


def _update_status(ticket: str, status_ru: str, comment: Optional[str]):
    try:
        with _lock:
            ws = _get_worksheet()
            cell = ws.find(ticket, in_column=1)
            if not cell:
                print(f"[Sheets] {ticket} not found — status not updated")
                return
            ws.update_cell(cell.row, STATUS_COL, status_ru)
            ws.update_cell(cell.row, UPDATED_COL, _now())
            if comment:
                ws.update_cell(cell.row, COMMENT_COL, comment)
        print(f"[Sheets] {ticket} → {status_ru}")
    except Exception as e:
        print(f"[Sheets] status update skipped: {e}")


def _spawn(target, *args):
    t = threading.Thread(target=target, args=args, daemon=True)
    t.start()
    return t


def export_shipment_async(chat_id: int, payload: Dict, price_eur: Optional[int] = None):
    if not sheets_enabled():
        print("[Sheets] GOOGLE_SA_JSON/SHEET_ID не заданы — экспорт пропущен")
        return None
    try:
        return _spawn(_append, build_row(chat_id, payload, price_eur))
    except Exception as e:
        print(f"[Sheets] export skipped: {e}")
        return None


def update_status_async(ticket: str, status_ru: str, comment: Optional[str] = None):
    if not sheets_enabled():
        return None
    try:
        return _spawn(_update_status, ticket, status_ru, comment)
    except Exception as e:
        print(f"[Sheets] status update skipped: {e}")
        return None
