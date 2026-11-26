# -*- coding: utf-8 -*-
"""Funnel bug fixes: UA names, phone normalization, /start logging, /stats,
DB-error handling, webhook marking, greeting calc intent, pending updates."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from test_ads_start_help import Base, msg, CHAT, m, h, fd, fpost
import docubridge_db_state as dbs
import docubridge_admin as adm


class TestName(unittest.TestCase):
    def test_valid(self):
        for n in ("Ігор Коваль", "Олексій", "ЇЖАК Євген", "Ґанна", "Мар’яна", "Мар'яна",
                  "Мар\u02bcяна", "Иван П.", "Анна-Мария", "John Smith", "Іа"):
            self.assertTrue(m.valid_name(n), n)

    def test_invalid(self):
        for n in ("", "И", "..", "'-", "Иван1", "+380501234567", "@user", "И."):
            self.assertFalse(m.valid_name(n), n)


class TestPhoneNormalize(unittest.TestCase):
    def test_cases(self):
        cases = {
            "067 123 45 67": "+380671234567",
            "(067) 123-45-67": "+380671234567",
            "380671234567": "+380671234567",
            "375291234567": "+375291234567",
            "79161234567": "+79161234567",
            "8 916 123-45-67": "+79161234567",
            "00353870000000": "+353870000000",
            "+353 87 000 0000": "+353870000000",
            "+36 30 123 4567": "+36301234567",
        }
        for raw, exp in cases.items():
            self.assertEqual(m.normalize_phone(raw), exp, raw)
            self.assertTrue(m.valid_phone(raw), raw)

    def test_still_invalid(self):
        for p in ("12345", "067123", "abc"):
            self.assertFalse(m.valid_phone(p), p)

    def test_mask(self):
        self.assertEqual(m.mask_phone("+380671234567"), "+380…67")
        self.assertNotIn("1234", m.mask_for_log("ask_phone", "+380671234567"))
        self.assertEqual(m.mask_for_log("ask_name", "Карина"), "К***")
        self.assertEqual(m.mask_for_log("ask_volume", "300 г"), "300 г")


class TestFunnelFixes(Base):
    def go_to_name(self):
        h.start(msg("/start ua_by"))
        for t in (m.BTN_CALC, "Доверенность", "до 100 г", m.BTN_APPLY, "Київ", "Мінськ"):
            h.any_text(msg(t))
        self.assertEqual(self.states[CHAT][0], "ask_name")

    def test_ukrainian_name_and_local_phone_create_ticket(self):
        self.go_to_name()
        h.any_text(msg("Ігор Коваль"))
        self.assertEqual(self.states[CHAT][0], "ask_phone")
        h.any_text(msg("067 123 45 67"))
        st, data = self.states[CHAT]
        self.assertEqual(st, "confirm")
        self.assertEqual(data["phone"], "+380671234567")
        with mock.patch.object(fpost, "create_shipment", return_value="DB4242"), \
                mock.patch.object(fpost, "notify_admin_lead"), \
                mock.patch.object(fpost, "export_shipment_async"):
            h.any_text(msg(m.BTN_CONFIRM))
        self.assertEqual(self.states[CHAT][0], "completed")
        self.assertEqual(self.states[CHAT][1]["ticket"], "DB4242")
        self.assertIn("DB4242", self.texts())

    def test_start_logged_and_recorded(self):
        u = msg("/start ua_ru"); u.from_user = SimpleNamespace(id=CHAT, username="tester")
        with mock.patch.object(h, "record_start_event") as rec, mock.patch("builtins.print") as pr:
            h.start(u)
        rec.assert_called_once_with(CHAT, "ua_ru", "tester")
        logged = " ".join(str(c.args[0]) for c in pr.call_args_list if c.args)
        self.assertIn("[Start] chat_id=555 payload=ua_ru username=@tester", logged)

    def test_bare_start_recorded_without_payload(self):
        with mock.patch.object(h, "record_start_event") as rec:
            h.start(msg("/start"))
        rec.assert_called_once_with(CHAT, None, None)

    def test_phone_not_logged_in_full(self):
        self.go_to_name(); h.any_text(msg("Иван"))
        with mock.patch("builtins.print") as pr:
            h.any_text(msg("+380671234567"))
        logged = " ".join(str(c.args[0]) for c in pr.call_args_list if c.args)
        self.assertNotIn("380671234567", logged)

    def test_db_error_does_not_reset(self):
        self.go_to_name()
        before = self.states[CHAT]
        with mock.patch.object(fd, "get_state", return_value=(m.DB_ERROR_STATE, {})):
            h.any_text(msg("Иван"))
        self.assertEqual(self.states[CHAT], before)
        self.assertIn("Техническая заминка", self.sent[-1][0])
        self.assertNotIn("Сессия сброшена", self.texts())

    def test_contact_on_db_error(self):
        c = msg(None); c.contact = SimpleNamespace(phone_number="380671234567")
        with mock.patch.object(h, "get_state", return_value=(m.DB_ERROR_STATE, {})):
            h.contact_shared(c)
        self.assertIn("Техническая заминка", self.sent[-1][0])

    def test_greeting_calc_intent_route_ref(self):
        # e.g. state lost/greeting but ref says ua_by → straight to doc type
        self.states[CHAT] = ("greeting", {"ref": "ua_by"})
        h.any_text(msg("Рассчитать точно"))
        st, data = self.states[CHAT]
        self.assertEqual(st, "ask_doc_type")
        self.assertEqual((data["from_country"], data["to_country"]), ("Украина", "Беларусь"))

    def test_greeting_calc_intent_no_ref(self):
        for t in ("Рассчитать", "расчет", "Расчёт стоимости", "цена?", "Сколько стоит"):
            self.states.clear(); self.sent.clear()
            self.states[CHAT] = ("greeting", {})
            h.any_text(msg(t))
            self.assertEqual(self.states[CHAT][0], "choose_route", t)
        self.ai_m.assert_not_called()


class TestDbState(unittest.TestCase):
    def test_get_state_retries_then_succeeds(self):
        good = mock.MagicMock()
        good.cursor.return_value.fetchone.return_value = {"state": "ask_name", "data": {"x": 1}}
        bad = mock.MagicMock()
        bad.cursor.return_value.execute.side_effect = Exception("SSL closed")
        with mock.patch.object(dbs, "DB_URL", "postgres://x"), \
                mock.patch.object(dbs, "get_conn", side_effect=[bad, good]), \
                mock.patch.object(dbs, "return_conn"), mock.patch.object(dbs, "_drop_conn") as drop:
            self.assertEqual(dbs.get_state(1), ("ask_name", {"x": 1}))
        drop.assert_called_once_with(bad)

    def test_get_state_fails_twice_returns_error_state(self):
        with mock.patch.object(dbs, "DB_URL", "postgres://x"), \
                mock.patch.object(dbs, "get_conn", return_value=None):
            self.assertEqual(dbs.get_state(1), (dbs.DB_ERROR_STATE, {}))

    def test_no_row_is_greeting(self):
        good = mock.MagicMock(); good.cursor.return_value.fetchone.return_value = None
        with mock.patch.object(dbs, "DB_URL", "postgres://x"), \
                mock.patch.object(dbs, "get_conn", return_value=good), mock.patch.object(dbs, "return_conn"):
            self.assertEqual(dbs.get_state(1), ("greeting", {}))

    def test_set_state_retries(self):
        good = mock.MagicMock()
        with mock.patch.object(dbs, "DB_URL", "postgres://x"), \
                mock.patch.object(dbs, "get_conn", side_effect=[None, good]), \
                mock.patch.object(dbs, "return_conn"):
            self.assertTrue(dbs.set_state(1, "ask_name", {}))
        good.commit.assert_called_once()


class TestWebhook(unittest.TestCase):
    def post(self, side_effect=None):
        body = json.dumps({"update_id": 77, "message": {
            "message_id": 1, "date": 0, "chat": {"id": 5, "type": "private"}, "text": "hi"}})
        with mock.patch.object(h, "is_update_processed", return_value=False), \
                mock.patch.object(h, "mark_update_processed") as mark, \
                mock.patch.object(h.bot, "process_new_updates", side_effect=side_effect):
            client = h.app.test_client()
            r = client.post(f"/webhook/{h.WEBHOOK_SECRET}", data=body,
                            headers={"content-type": "application/json"})
        return r, mark

    def test_marked_after_success(self):
        r, mark = self.post()
        self.assertEqual(r.status_code, 200)
        mark.assert_called_once_with(77)

    def test_not_marked_on_failure(self):
        r, mark = self.post(side_effect=RuntimeError("boom"))
        self.assertEqual(r.status_code, 200)
        mark.assert_not_called()

    def test_pending_updates_kept(self):
        with mock.patch.object(h, "WEBHOOK_BASE", "https://x"), \
                mock.patch.object(h.bot, "remove_webhook"), \
                mock.patch.object(h.bot, "set_webhook", return_value=True) as sw:
            h.ensure_webhook()
        self.assertFalse(sw.call_args.kwargs["drop_pending_updates"])


class TestStats(unittest.TestCase):
    def sample(self, days, starts=10, chats=8, orders=2):
        return {"days": days, "starts": starts, "start_chats": chats, "orders": orders,
                "by_payload": [("ua_ru", 6), ("(без метки)", 4)],
                "by_route": [("Беларусь → Украина", 2)],
                "by_state": [("route_offer", 5), ("ask_name", 1)]}

    def test_format(self):
        txt = adm.format_stats([self.sample(1, 1, 1, 0), self.sample(7), self.sample(30)], self.sample(30))
        for w in ("За 1 дн.", "За 7 дн.", "За 30 дн.", "ua_ru: 6", "Беларусь → Украина: 2",
                  "оффер (не нажал «Рассчитать»): 5", "имя: 1", "конверсия 25%"):
            self.assertIn(w, txt)

    def test_no_db(self):
        self.assertIn("нет подключения", adm.format_stats([None], None))

    def test_admin_only_and_periods(self):
        sent = []
        with mock.patch.object(adm, "ADMIN_CHAT_ID", 999), \
                mock.patch.object(adm, "get_funnel_stats", side_effect=lambda d: self.sample(d)) as gs, \
                mock.patch.object(adm.bot, "send_message", side_effect=lambda c, t, **k: sent.append(t)):
            self.assertFalse(adm.is_admin(555))
            self.assertTrue(adm.is_admin(999))
            adm.admin_stats(SimpleNamespace(text="/stats", chat=SimpleNamespace(id=999)))
            self.assertEqual([c.args[0] for c in gs.call_args_list], [1, 7, 30])
            gs.reset_mock()
            adm.admin_stats(SimpleNamespace(text="/stats 3", chat=SimpleNamespace(id=999)))
            self.assertEqual([c.args[0] for c in gs.call_args_list], [3])
        self.assertIn("За 30 дн.", sent[0])
        self.assertIn("stats", m.KNOWN_COMMANDS)


if __name__ == "__main__":
    unittest.main()
