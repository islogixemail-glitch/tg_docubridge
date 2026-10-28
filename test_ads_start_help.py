# -*- coding: utf-8 -*-
"""Telegram Ads fix: /start hub, deep-links, /help, fallback — mocked bot/DB."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ["DOCUBRIDGE_SKIP_STARTUP"] = "1"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST_TOKEN_FOR_UNIT_TESTS")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as m  # noqa: E402
import docubridge_handlers as h  # noqa: E402
import docubridge_flow_steps as fs  # noqa: E402
import docubridge_flow_dialog as fd  # noqa: E402
import docubridge_flow_dialog_pre as fpre  # noqa: E402
import docubridge_flow_dialog_post as fpost  # noqa: E402

CHAT = 555


def msg(text):
    return SimpleNamespace(text=text, chat=SimpleNamespace(id=CHAT), from_user=SimpleNamespace(id=CHAT))


class Base(unittest.TestCase):
    def setUp(self):
        self.states = {}
        self.sent = []

        def set_state(cid, st, data=None):
            self.states[cid] = (st, dict(data or {}))

        def get_state(cid):
            return self.states.get(cid, (None, {}))

        def send(cid, text, reply_markup=None, **kw):
            self.sent.append((text, reply_markup))

        self.patches = []
        for mod in (h, fs, fd, fpre, fpost):
            for name, val in (("set_state", set_state), ("get_state", get_state),
                              ("save_message", lambda *a, **k: None)):
                if hasattr(mod, name):
                    p = mock.patch.object(mod, name, val); p.start(); self.patches.append(p)
        p = mock.patch.object(m.bot, "send_message", side_effect=send); p.start(); self.patches.append(p)
        p = mock.patch.object(m.bot, "answer_callback_query", return_value=None); p.start(); self.patches.append(p)
        self.ai = mock.patch.object(fpost, "ai_reply", return_value="Ответ AI"); self.ai_m = self.ai.start()
        self.patches.append(self.ai)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def texts(self):
        return "\n".join(t for t, _ in self.sent)


class TestStart(Base):
    def test_plain_start_shows_hub_not_route(self):
        h.start(msg("/start"))
        self.assertEqual(self.states[CHAT][0], "greeting")
        self.assertIn("Выберите маршрут", self.texts())
        self.assertNotIn("Из какого города", self.texts())
        kb = self.sent[0][1]
        self.assertIsInstance(kb, m.InlineKeyboardMarkup)
        cbs = [b.callback_data for row in kb.keyboard for b in row]
        self.assertEqual(cbs, ["hub:ua_ru", "hub:ua_by", "hub:ru_ua", "hub:by_ua"])
        self.assertIsInstance(self.sent[-1][1], m.ReplyKeyboardMarkup)

    def test_dokumenty_shows_hub(self):
        h.start(msg("/start dokumenty"))
        self.assertEqual(self.states[CHAT][0], "greeting")
        self.assertEqual(self.states[CHAT][1]["ref"], "dokumenty")
        self.assertIn("витрины", self.texts())
        self.assertNotIn("Из какого города", self.texts())

    def test_route_deeplinks(self):
        for key, (fc, tc) in m.START_PAYLOAD_ROUTES.items():
            self.sent.clear()
            h.start(msg(f"/start {key}"))
            st, data = self.states[CHAT]
            self.assertEqual(st, "ask_from_city")
            self.assertEqual((data["from_country"], data["to_country"]), (fc, tc))
            self.assertIn(f"Маршрут: {fc} → {tc}", self.texts())

    def test_hub_button_enters_route(self):
        h.start(msg("/start dokumenty"))
        call = SimpleNamespace(id="1", data="hub:by_ua", message=msg(""), from_user=SimpleNamespace(id=CHAT))
        h.hub_route_cb(call)
        st, data = self.states[CHAT]
        self.assertEqual(st, "ask_from_city")
        self.assertEqual(data["to_country"], "Украина")
        self.assertEqual(data["ref"], "dokumenty")

    def test_hub_callback_parse(self):
        self.assertIsNone(m.parse_hub_callback("st:DB1:accepted"))
        self.assertIsNone(m.parse_hub_callback("hub:ru_by"))
        self.assertEqual(m.parse_hub_callback("hub:ua_ru"), ("Украина", "Россия"))


class TestHelpFallback(Base):
    def test_help(self):
        h.help_cmd(msg("/help"))
        t = self.texts()
        for w in ("паспорта", "ID-карты", "военные билеты", "расчёт", "/start"):
            self.assertIn(w, t)

    def test_unknown_command(self):
        h.any_text(msg("/foobar"))
        self.assertIn("/start", self.texts()); self.assertIn("/help", self.texts())
        self.assertIsInstance(self.sent[-1][1], m.ReplyKeyboardMarkup)

    def test_command_parsing(self):
        self.assertEqual(m.command_name("/Help@islogix_bot x"), "help")
        self.assertFalse(m.is_unknown_command("/orders"))
        self.assertFalse(m.is_unknown_command("привет"))
        self.assertTrue(m.is_unknown_command("/xyz"))

    def test_gibberish_in_greeting(self):
        h.start(msg("/start")); self.sent.clear()
        h.any_text(msg("asdkjhqwe"))
        self.assertEqual(self.texts(), m.FALLBACK_TEXT)
        self.ai_m.assert_not_called()
        h.any_text(msg("ццццц ккккк"))
        self.ai_m.assert_not_called()

    def test_real_question_uses_ai_with_hint(self):
        h.start(msg("/start")); self.sent.clear()
        h.any_text(msg("сколько стоит доставка диплома"))
        self.assertIn("Ответ AI", self.texts()); self.assertIn("/help", self.texts())

    def test_quote_flow_intact(self):
        h.start(msg("/start ua_ru"))
        for t in ("Киев", "Москва", "диплом", "300 г", "обычная"):
            h.any_text(msg(t))
        self.assertEqual(self.states[CHAT][0], "quote_shown")
        self.assertIn("€140", self.texts())

    def test_military_id_rejected(self):
        h.start(msg("/start ua_by"))
        for t in ("Киев", "Минск", "военный билет"):
            h.any_text(msg(t))
        self.assertEqual(self.states[CHAT][0], "ask_doc_type")
        self.assertIn("военные билеты", self.sent[-1][0])


if __name__ == "__main__":
    unittest.main()
