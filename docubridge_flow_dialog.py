# -*- coding: utf-8 -*-
"""DocuBridge dialog handle_answer — Phase 2."""
from docubridge_runtime import *  # noqa: F401,F403
from docubridge_flow_dialog_pre import handle_answer_pre
from docubridge_flow_dialog_post import handle_answer_post


def handle_answer(chat_id: int, text: str):
    print(f"[Handler] handle_answer: chat_id={chat_id}, text={text!r}")
    state, data = get_state(chat_id)
    data = dict(data or {})
    save_message(chat_id, text, None)
    if handle_answer_pre(chat_id, text, state, data):
        return
    handle_answer_post(chat_id, text, state, data)
