# -*- coding: utf-8 -*-
"""Funnel convenience: city/name/contact buttons, progress, «Назад», manager on
every step, off-step questions, «Исправить», «Что дальше», reminders, honest /stats."""
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from test_ads_start_help import Base, CHAT, m, h, fun
import docubridge_admin as adm
import docubridge_reminders as rem
import docubridge_db_reminders as dbr
import docubridge_db_pool_a as pa
import docubridge_flow_dialog_post as fpost


def kb_texts(kb):
    if kb is None or not hasattr(kb, "keyboard"):
        return []
    return [(b if isinstance(b, dict) else b.to_dict())["text"] for row in kb.keyboard for b in row]


def kb_raw(kb):
    return [(b if isinstance(b, dict) else b.to_dict()) for row in kb.keyboard for b in row]


USER = NS(id=CHAT, username="u", first_name="Ірина", last_name="Коваль")


def umsg(text, user=USER):
    return NS(text=text, chat=NS(id=CHAT), from_user=user)


class FunnelBase(Base):
    def say(self, *texts, user=USER):
        for t in texts:
            h.any_text(umsg(t, user))

    def to_quote(self, route="ua_by"):
        with mock.patch.object(h, "record_start_event"):
            h.start(umsg(f"/start {route}"))
        self.say(m.BTN_CALC, "Доверенность", "до 100 г")
        self.assertEqual(self.state(), "quote_shown")

    def to_confirm(self):
        self.to_quote()
        self.say(m.BTN_APPLY, "Киев", "Минск", "✅ Ірина Коваль", "067 123 45 67")
        self.assertEqual(self.state(), "confirm")

    def state(self):
        return self.states[CHAT][0]

    def data(self):
        return self.states[CHAT][1]

    def last(self):
        return self.sent[-1]


class TestCityButtons(FunnelBase):
    def test_from_and_to_city_buttons_by_country(self):
        self.to_quote("ua_by"); self.say(m.BTN_APPLY)
        text, kb = self.last()
        self.assertIn("Шаг 1 из 4", text)
        for c in ("Киев", "Одесса", "Харьков", "Днепр", "Львов", m.BTN_OTHER_CITY, m.BTN_BACK, m.MENU_MANAGER):
            self.assertIn(c, kb_texts(kb))
        self.say("Одесса")
        text, kb = self.last()
        self.assertIn("Шаг 2 из 4", text)
        for c in ("Минск", "Гомель", "Брест"):
            self.assertIn(c, kb_texts(kb))
        self.assertEqual(self.data()["from_city"], "Одесса")

    def test_russia_cities_and_exact_button_text(self):
        self.to_quote("ua_ru"); self.say(m.BTN_APPLY, "Львов")
        self.assertIn("Москва", kb_texts(self.last()[1]))
        self.say("Ростов-на-Дону")
        self.assertEqual(self.data()["to_city"], "Ростов-на-Дону")

    def test_other_city_button_then_free_text(self):
        self.to_quote(); self.say(m.BTN_APPLY, m.BTN_OTHER_CITY)
        self.assertEqual(self.state(), "ask_from_city")
        self.assertIn("Напишите название города", self.last()[0])
        self.say("ужгород")
        self.assertEqual(self.data()["from_city"], "Ужгород")
        self.assertEqual(self.state(), "ask_to_city")


class TestNameAndPhone(FunnelBase):
    def test_profile_name_button(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск")
        text, kb = self.last()
        self.assertIn("Шаг 3 из 4", text)
        self.assertIn("✅ Ірина Коваль", kb_texts(kb))
        self.say("✅ Ірина Коваль")
        self.assertEqual(self.data()["name"], "Ірина Коваль")
        self.assertEqual(self.state(), "ask_phone")

    def test_other_name_typed(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск", "Олег")
        self.assertEqual(self.data()["name"], "Олег")

    def test_no_button_for_emoji_profile_name(self):
        self.to_quote(); self.data().pop("tg_name", None)
        odd = NS(id=CHAT, username=None, first_name="🔥🔥", last_name=None)
        self.say(m.BTN_APPLY, "Киев", "Минск", user=odd)
        self.assertEqual(kb_texts(self.last()[1]), [m.BTN_BACK, m.MENU_MANAGER])
        self.assertIsNone(m.profile_name(odd))

    def test_invalid_name_friendly(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск", "12")
        self.assertEqual(self.state(), "ask_name")
        self.assertIn("Напишите имя буквами", self.last()[0])
        self.assertIn("✅ Ірина Коваль", kb_texts(self.last()[1]))

    def test_phone_keyboard_contact_first(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск", "Олег")
        text, kb = self.last()
        self.assertIn("Шаг 4 из 4", text)
        first = kb_raw(kb)[0]
        self.assertEqual(first["text"], "📱 Отправить мой номер")
        self.assertTrue(first.get("request_contact"))

    def test_bad_phone_friendly_with_examples(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск", "Олег", "12345")
        self.assertEqual(self.state(), "ask_phone")
        self.assertIn("например", self.last()[0])
        self.assertIn("067 123 45 67", self.last()[0])
        self.assertIn(m.BTN_SHARE_CONTACT, kb_texts(self.last()[1]))

    def test_contact_button(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск", "Олег")
        c = umsg(None); c.contact = NS(phone_number="380671234567")
        h.contact_shared(c)
        self.assertEqual(self.state(), "confirm")
        self.assertEqual(self.data()["phone"], "+380671234567")


class TestBackAndManager(FunnelBase):
    def test_back_walks_every_step(self):
        self.to_confirm()
        for expected in ("ask_phone", "ask_name", "ask_to_city", "ask_from_city",
                         "quote_shown", "ask_volume", "ask_doc_type", "route_offer"):
            self.say(m.BTN_BACK)
            self.assertEqual(self.state(), expected)
        self.say(m.BTN_BACK)
        self.assertEqual(self.state(), "greeting")
        self.assertEqual(self.data(), {})

    def test_back_keeps_entered_data(self):
        self.to_confirm(); self.say(m.BTN_BACK, m.BTN_BACK)
        self.assertEqual(self.data()["from_city"], "Киев")
        self.assertEqual(self.data()["phone"], "+380671234567")

    def test_manager_keeps_step_on_every_step(self):
        self.to_quote(); self.say(m.BTN_APPLY)
        for st_before in ("ask_from_city",):
            self.say(m.MENU_MANAGER)
            self.assertEqual(self.state(), st_before)
            self.assertIn(m.MANAGER_CONTACT, self.last()[0])
            self.assertIn("Из какого города", self.last()[0])
        self.say("Киев", m.MENU_MANAGER)
        self.assertEqual(self.state(), "ask_to_city")
        self.assertIn(m.MANAGER_CONTACT, self.last()[0])

    def test_manager_on_offer_and_confirm(self):
        with mock.patch.object(h, "record_start_event"):
            h.start(umsg("/start ua_ru"))
        self.say(m.MENU_MANAGER)
        self.assertEqual(self.state(), "route_offer")
        self.assertIn(m.MANAGER_CONTACT, self.last()[0])
        self.states.clear(); self.to_confirm(); self.say(m.MENU_MANAGER)
        self.assertEqual(self.state(), "confirm")

    def test_every_funnel_keyboard_has_manager(self):
        for kb in (m.doc_type_keyboard(), m.volume_keyboard(), m.city_keyboard("Украина"),
                   m.name_keyboard("Ира"), m.name_keyboard(None), m.phone_keyboard(),
                   m.quote_keyboard(), m.confirm_keyboard()):
            self.assertIn(m.MENU_MANAGER, kb_texts(kb))


class TestOffStepQuestions(FunnelBase):
    def test_question_on_city_answers_and_keeps_buttons(self):
        self.to_quote(); self.say(m.BTN_APPLY, "а это надёжно?")
        self.assertEqual(self.state(), "ask_from_city")
        self.assertNotIn("from_city", self.data())
        text, kb = self.last()
        self.assertIn("Ответ AI", text)
        self.assertIn("Из какого города", text)
        self.assertIn("Киев", kb_texts(kb))

    def test_price_question_on_name_canned(self):
        self.to_quote(); self.say(m.BTN_APPLY, "Киев", "Минск", "сколько стоит?")
        self.assertEqual(self.state(), "ask_name")
        self.assertIn("€140", self.last()[0])
        self.fai_m.assert_not_called()

    def test_question_on_phone_and_quote_and_confirm(self):
        self.to_quote(); self.say("когда заберёте?")
        self.assertEqual(self.state(), "quote_shown")
        self.say(m.BTN_APPLY, "Киев", "Минск", "Олег", "а это безопасно?")
        self.assertEqual(self.state(), "ask_phone")
        self.assertIn("Ответ AI", self.last()[0])
        self.say("+380671234567", "кто привезёт?")
        self.assertEqual(self.state(), "confirm")
        self.assertIn("Проверьте заявку", self.last()[0])

    def test_ai_unavailable_falls_back_to_manager(self):
        self.fai_m.return_value = "Сейчас умные ответы временно недоступны."
        self.to_quote(); self.say(m.BTN_APPLY, "а документы страхуются?")
        self.assertIn(m.MANAGER_CONTACT, self.last()[0])
        self.assertEqual(self.state(), "ask_from_city")


class TestEditAndFinish(FunnelBase):
    def test_edit_each_field_returns_to_confirm(self):
        self.to_confirm()
        for field, value, key, exp in (
            ("Город отправки", "Львов", "from_city", "Львов"),
            ("Город доставки", "Гомель", "to_city", "Гомель"),
            ("Имя", "Олег Петров", "name", "Олег Петров"),
            ("Телефон", "+375291234567", "phone", "+375291234567"),
        ):
            self.say(m.BTN_EDIT)
            self.assertEqual(self.state(), "edit_choose")
            self.say(field, value)
            self.assertEqual(self.state(), "confirm", field)
            self.assertEqual(self.data()[key], exp)
            self.assertNotIn("edit_return", self.data())
        self.assertIn("Львов", self.last()[0]); self.assertIn("Гомель", self.last()[0])

    def test_edit_doc_and_weight(self):
        self.to_confirm(); self.say(m.BTN_EDIT, "Тип документа и вес", "Свидетельство")
        self.assertEqual(self.state(), "ask_volume")
        self.say("до 300 г")
        self.assertEqual(self.state(), "confirm")
        self.assertEqual(self.data()["doc_type"], "Свидетельство")
        self.assertEqual(self.data()["weight_grams"], 300)

    def test_edit_back_to_summary(self):
        self.to_confirm(); self.say(m.BTN_EDIT, m.BTN_EDIT_DONE)
        self.assertEqual(self.state(), "confirm")

    def test_confirm_summary_mentions_edit(self):
        self.to_confirm()
        self.assertIn("Исправить", self.last()[0])
        self.assertIn(m.BTN_EDIT, kb_texts(self.last()[1]))

    def test_thanks_what_next(self):
        self.to_confirm()
        with mock.patch.object(fpost, "create_shipment", return_value="DB777"), \
                mock.patch.object(fpost, "notify_admin_lead"), mock.patch.object(fpost, "export_shipment_async"):
            self.say(m.BTN_CONFIRM)
        t = self.sent[-3][0]
        for w in ("DB777", "Что дальше", "Оплата", "Передача документов", "/track", "€140"):
            self.assertIn(w, t)
        self.assertNotIn("Менеджер свяжется", t)
        self.assertIn("DB777", self.last()[0])
        self.assertEqual(self.state(), "completed")

    def test_legacy_chat_without_doc_goes_to_doc_then_summary(self):
        # old city-first funnel: cities known, no doc/weight
        self.states[CHAT] = ("ask_phone", {"from_country": "Украина", "to_country": "Россия",
                                           "from_city": "Киев", "to_city": "Москва", "name": "Олег"})
        self.say("+380671234567")
        self.assertEqual(self.state(), "ask_doc_type")
        self.say("Доверенность", "до 100 г")
        self.assertEqual(self.state(), "confirm")
        self.assertIn("Киев", self.last()[0])


class TestReminders(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(rem, "prompt_step"); self.prompt = self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_sends_once_with_prefix(self):
        rows = [{"chat_id": 11, "state": "ask_phone", "data": {"from_country": "Украина"}}]
        with mock.patch.object(rem, "find_reminder_candidates", return_value=rows) as f, \
                mock.patch.object(rem, "claim_reminder", return_value=True) as c, \
                mock.patch.object(rem, "ADMIN_CHAT_ID", 0):
            self.assertEqual(rem.run_reminders_once(), 1)
        f.assert_called_once_with(rem.REMIND_STATES, rem.REMIND_AFTER_MIN, 48)
        c.assert_called_once_with(11, "ask_phone")
        args, kw = self.prompt.call_args
        self.assertEqual(args[:2], (11, "ask_phone"))
        self.assertIn("не закончили", kw["prefix"]); self.assertIn("телефон", kw["prefix"])

    def test_already_claimed_or_admin_skipped(self):
        rows = [{"chat_id": 11, "state": "ask_name", "data": {}}, {"chat_id": 99, "state": "ask_name", "data": {}}]
        with mock.patch.object(rem, "find_reminder_candidates", return_value=rows), \
                mock.patch.object(rem, "claim_reminder", return_value=False) as c, \
                mock.patch.object(rem, "ADMIN_CHAT_ID", 99):
            self.assertEqual(rem.run_reminders_once(), 0)
        c.assert_called_once_with(11, "ask_name")
        self.prompt.assert_not_called()

    def test_send_failure_is_swallowed(self):
        self.prompt.side_effect = RuntimeError("bot was blocked by the user")
        rows = [{"chat_id": 11, "state": "confirm", "data": {}}]
        with mock.patch.object(rem, "find_reminder_candidates", return_value=rows), \
                mock.patch.object(rem, "claim_reminder", return_value=True):
            self.assertEqual(rem.run_reminders_once(), 0)

    def test_states_and_window(self):
        self.assertNotIn("completed", rem.REMIND_STATES)
        self.assertNotIn("greeting", rem.REMIND_STATES)
        self.assertIn("ask_from_city", rem.REMIND_STATES)
        self.assertEqual(rem.REMIND_MAX_AGE_H, 48)
        self.assertTrue(60 <= rem.REMIND_AFTER_MIN <= 120)

    def test_disabled_by_env(self):
        with mock.patch.dict("os.environ", {"DOCUBRIDGE_REMINDERS": "0"}), \
                mock.patch.object(rem.threading, "Thread") as th:
            self.assertIsNone(rem.start_reminder_thread())
        th.assert_not_called()

    def test_sql_excludes_reminded_and_ordered(self):
        conn = mock.MagicMock(); cur = conn.cursor.return_value; cur.fetchall.return_value = []
        with mock.patch.object(dbr, "DB_URL", "postgres://x"), \
                mock.patch.object(dbr, "get_conn", return_value=conn), mock.patch.object(dbr, "return_conn"):
            dbr.find_reminder_candidates(["ask_name"], 90, 48)
        sql, params = cur.execute.call_args.args
        self.assertIn("funnel_reminders", sql); self.assertIn("shipments", sql)
        self.assertEqual(params, (["ask_name"], 90, 48, 20))

    def test_claim_is_atomic_insert(self):
        conn = mock.MagicMock(); cur = conn.cursor.return_value; cur.fetchone.return_value = (11,)
        with mock.patch.object(dbr, "DB_URL", "postgres://x"), \
                mock.patch.object(dbr, "get_conn", return_value=conn), mock.patch.object(dbr, "return_conn"):
            self.assertTrue(dbr.claim_reminder(11, "ask_name"))
        self.assertIn("ON CONFLICT (chat_id) DO NOTHING", cur.execute.call_args.args[0])

    def test_pool_threadsafe_and_table_created(self):
        with mock.patch.object(pa, "DB_URL", "postgres://x"), \
                mock.patch.object(pa.pool, "ThreadedConnectionPool") as tp:
            pa.init_db_pool()
        tp.assert_called_once()
        conn = mock.MagicMock()
        with mock.patch.object(pa, "DB_URL", "postgres://x"), \
                mock.patch.object(pa, "get_conn", return_value=conn), mock.patch.object(pa, "return_conn"):
            pa.ensure_tables()
        sqls = " ".join(c.args[0] for c in conn.cursor.return_value.execute.call_args_list)
        self.assertIn("CREATE TABLE IF NOT EXISTS funnel_reminders", sqls)


class TestStatsConversion(unittest.TestCase):
    def st(self, days, chats, orders, tracked, reminders=0):
        return {"days": days, "starts": chats, "start_chats": chats, "orders": orders,
                "orders_tracked": tracked, "reminders": reminders,
                "by_payload": [], "by_route": [], "by_state": [("edit_choose", 1)]}

    def test_conversion_uses_tracked_orders(self):
        s = self.st(30, 4, 12, 1, reminders=2)
        txt = adm.format_stats([s], s)
        self.assertIn("конверсия 25%", txt)
        self.assertIn("заявок 12 (из них после старта с меткой/логом: 1)", txt)
        self.assertIn("напоминаний 2", txt)
        self.assertIn("исправление заявки: 1", txt)

    def test_conversion_capped_at_100(self):
        s = self.st(1, 1, 3, 3)
        self.assertIn("конверсия 100%", adm.format_stats([s], s))

    def test_stats_sql_has_tracked_and_reminders(self):
        import docubridge_db_stats as ds
        conn = mock.MagicMock(); cur = conn.cursor.return_value
        cur.fetchall.side_effect = [[(5, 4)], [], [(2,)], [(1,)], [(0,)], [], []]
        with mock.patch.object(ds, "DB_URL", "postgres://x"), \
                mock.patch.object(ds, "get_conn", return_value=conn), mock.patch.object(ds, "return_conn"):
            r = ds.get_funnel_stats(7)
        self.assertEqual((r["orders"], r["orders_tracked"], r["reminders"]), (2, 1, 0))
        sqls = " ".join(c.args[0] for c in cur.execute.call_args_list)
        self.assertIn("EXISTS (SELECT 1 FROM start_events e", sqls)


if __name__ == "__main__":
    unittest.main()
