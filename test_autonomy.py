# -*- coding: utf-8 -*-
"""Bot leads the client alone (PR #16): «Что дальше» by origin, terms → address request,
currency + approved UAH details, payment after arrival, admin payment request, handoff
(24 h) only on triggers, attachments, RU/UA language."""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest import mock

from test_ads_start_help import CHAT, m, fs, fpost
from test_funnel_complete import FunnelBase, USER
import docubridge_handoff as ho
import docubridge_next_steps as ns
import docubridge_admin as adm
import docubridge_db_notify as ndb
import docubridge_runtime as rt
import docubridge_lang as L
import docubridge_payment_config as pcfg

GROUP = -5556349052
ENV_OFF = {"LEADS_CHAT_ID": str(GROUP), "DOCUBRIDGE_PAY_CURRENCIES": "",
           "DOCUBRIDGE_PAYMENT_TEXT_UAH": "", "DOCUBRIDGE_PAYMENT_TEXT_EUR": "", "DOCUBRIDGE_PAYMENT_TEXT_RUB": "",
           "DOCUBRIDGE_HANDOVER_TEXT_UA": "", "DOCUBRIDGE_HANDOVER_TEXT_RUBY": "",
           "DOCUBRIDGE_HANDOVER_TEXT_UA_UK": "", "DOCUBRIDGE_HANDOVER_TEXT_RUBY_UK": ""}
UAH_TEXT = (
    "Отримувач: ФОП Саєнко Ігор\n"
    "IBAN: UA853220010000026002310087318\n"
    "ІПН/ЄДРПОУ: 2468719172\n"
    "Банк: Акціонерне товариство УНІВЕРСАЛ БАНК, МФО 322001, ЄДРПОУ банку 21133352\n"
    "Призначення платежу: за послуги з організації доставки"
)
UA_DATA = {"ticket": "DB7777", "from_country": "Украина", "to_country": "Беларусь", "weight_grams": 100}
RU_DATA = {"ticket": "DB7777", "from_country": "Россия", "to_country": "Украина", "weight_grams": 100}


def cb(data, chat=CHAT, user=USER):
    return NS(id="1", data=data, message=NS(chat=NS(id=chat, type="private")), from_user=user)


class Base(FunnelBase):
    def setUp(self):
        super().setUp()
        ho._mem_last.clear()
        L.reset()
        for name in ("get_state", "set_state", "save_message"):
            p = mock.patch.object(ho, name, getattr(fs, name)); p.start(); self.patches.append(p)
        self.env = mock.patch.dict(os.environ, ENV_OFF); self.env.start(); self.patches.append(self.env)
        self.fwd = mock.patch.object(m.bot, "forward_message"); self.fwd_m = self.fwd.start()
        self.patches.append(self.fwd)

    def calls(self, target):
        return [c for c in m.bot.send_message.call_args_list if c.args[0] == target]

    def group_cards(self):
        return [c.args[1] for c in self.calls(GROUP)]

    def client_msgs(self):
        return [c.args[1] for c in self.calls(CHAT)]

    def client_kb(self, i=-1):
        return self.calls(CHAT)[i].kwargs.get("reply_markup")

    def confirm(self, ticket="DB7777"):
        with mock.patch.object(fpost, "create_shipment", return_value=ticket) as cs, \
                mock.patch.object(fpost, "notify_admin_lead"), mock.patch.object(fpost, "export_shipment_async"):
            self.say(m.BTN_CONFIRM)
        return cs

    def order(self, route="ua_by", cities=("Киев", "Минск")):
        self.to_quote(route)
        self.say(m.BTN_APPLY, *cities, "✅ Ірина Коваль", "067 123 45 67")
        self.confirm()


def cbs(kb):
    return [b.callback_data for r in kb.keyboard for b in r]


def labels(kb):
    return [b.text for r in kb.keyboard for b in r]


class TestWhatNextByOrigin(Base):
    def test_from_ukraine(self):
        self.order("ua_by")
        thanks, terms, curq = self.client_msgs()[-3:]
        self.assertIn("Номер отправления: DB7777", thanks)
        self.assertIn("1. Передача документов: отправьте документы своими силами и за свой счёт на адрес "
                      "нашего человека в Словакии — Новой Почтой или другим удобным способом.", thanks)
        self.assertIn("2. Оплата: 100% предоплата после того, как документы поступят в Братиславу.", thanks)
        self.assertLess(thanks.index("Передача документов"), thanks.index("Оплата"))
        self.assertLess(thanks.index("Оплата"), thanks.index("Статусы"))
        self.assertIn("€140 (конверт до 500 г)", terms)
        self.assertIn("10–14 рабочих дней", terms)
        self.assertIn("своими силами и за свой счёт в Словакию", terms)
        self.assertIn("100% после поступления документов в Братиславу", terms)
        self.assertEqual(labels(self.client_kb(-2)), ["✅ Условия подходят, нужен адрес"])
        self.assertEqual(cbs(self.client_kb(-2)), ["tc:DB7777"])
        self.assertEqual(curq, "В какой валюте будете оплачивать заявку DB7777? "
                               "Оплата — после того, как документы поступят в Братиславу.")
        self.assertEqual(cbs(self.client_kb()), ["pc:DB7777:UAH", "pc:DB7777:EUR", "pc:DB7777:RUB",
                                                 "ns:hand:DB7777", "ns:track:DB7777", "ns:human:DB7777"])
        self.assertEqual(self.group_cards(), [])          # no «не настроено» alert any more
        for w in ("пришлём отдельным", "Москв"):
            self.assertNotIn(w, thanks)
        self.assertEqual(self.data()["from_country"], "Украина")

    def test_from_russia_belarus(self):
        self.order("by_ua", ("Минск", "Киев"))
        thanks, terms, curq = self.client_msgs()[-3:]
        self.assertIn("отправьте документы своими силами и за свой счёт в Москву — например, СДЭКом.", thanks)
        self.assertIn("после того, как документы поступят нашему агенту в Москве", thanks)
        self.assertIn("за свой счёт в Москву", terms)
        self.assertIn("нашему агенту в Москве", curq)
        for w in ("Словак", "Братислав"):
            self.assertNotIn(w, thanks)

    def test_handover_env_override(self):
        with mock.patch.dict(os.environ, {"DOCUBRIDGE_HANDOVER_TEXT_UA": "Своя инструкция UA",
                                          "DOCUBRIDGE_HANDOVER_TEXT_RUBY": "Своя инструкция RU"}):
            self.assertEqual(pcfg.handover_text("UA", "ru"), "Своя инструкция UA")
            self.assertEqual(pcfg.handover_text("UA", "uk"), "Своя инструкция UA")
            self.assertEqual(pcfg.handover_text("RUBY", "ru"), "Своя инструкция RU")
            with mock.patch.dict(os.environ, {"DOCUBRIDGE_HANDOVER_TEXT_UA_UK": "Інструкція"}):
                self.assertEqual(pcfg.handover_text("UA", "uk"), "Інструкція")

    def test_overweight_no_price_named_and_human(self):
        self.to_quote()
        self.states[CHAT][1]["weight_grams"] = 900
        self.say(m.BTN_APPLY, "Киев", "Минск", "✅ Ірина Коваль", "067 123 45 67")
        self.confirm()
        thanks, terms, _ = self.client_msgs()[-3:]
        self.assertIn("Стоимость: обсуждается с менеджером (вес больше 0,5 кг) — он ответит в течение 24 часов.", thanks)
        self.assertNotIn("€", thanks.split("Стоимость:")[1].split("\n")[0])
        self.assertIn("обсуждается с менеджером", terms)
        self.assertTrue(any("вес больше 0,5 кг" in c for c in self.group_cards()))

    def test_no_invented_payment_facts(self):
        t = ns.thanks_text("DB1", "A → B", UA_DATA).lower()
        for w in ("грн", "курс", "скидк", "свяжется с вами по телефону"):
            self.assertNotIn(w, t)


class TestTerms(Base):
    def setUp(self):
        super().setUp()
        self.at = datetime(2026, 11, 26, 21, 30, tzinfo=timezone.utc)

    def press(self, res, ticket="DB7777", user=USER):
        with mock.patch.object(ndb, "accept_terms", return_value=res) as at:
            ho.terms_cb(cb(f"tc:{ticket}", user=user))
        return at

    def test_card_once_and_second_press(self):
        at = self.press({"first": True, "at": self.at, "payload": dict(UA_DATA, name="Ірина")})
        at.assert_called_once_with("DB7777", CHAT)
        self.assertEqual(self.client_msgs()[-1], "Спасибо! Запрос адреса передан менеджеру — "
                                                 "он пришлёт адрес в этом чате в течение 24 часов.")
        cards = self.group_cards()
        self.assertEqual(len(cards), 1)
        lines = cards[0].split("\n")
        self.assertEqual(lines[0], "📬 DB7777: клиент согласен, нужен адрес (UA→SK)")
        for w in ("Куда нужен адрес: Словакия (наш человек)", "Клиент: @u", "Chat ID: 555",
                  "Условия приняты: 26.11.2026 23:30 (Киев)", "/order DB7777", "Язык клиента: RU"):
            self.assertIn(w, cards[0])
        self.press({"first": False, "at": self.at, "payload": UA_DATA})
        self.assertEqual(len(self.group_cards()), 1)
        self.assertIn("уже передан менеджеру", self.client_msgs()[-1])

    def test_ru_by_card_tag(self):
        self.press({"first": True, "at": self.at, "payload": RU_DATA})
        self.assertTrue(self.group_cards()[0].startswith("📬 DB7777: клиент согласен, нужен адрес (RU-BY→Москва)"))
        self.assertIn("Куда нужен адрес: Москва (наш агент)", self.group_cards()[0])

    def test_not_limited_by_handoff_throttle(self):
        self.say("позовите менеджера")
        self.press({"first": True, "at": self.at, "payload": UA_DATA})
        self.assertEqual(len(self.group_cards()), 2)

    def test_foreign_order_keeps_state(self):
        self.states[CHAT] = ("ask_name", {"x": 1})
        self.press(False, ticket="DB1111")
        self.assertEqual(self.states[CHAT], ("ask_name", {"x": 1}))
        self.assertIn("не найдена", self.client_msgs()[-1])
        self.assertEqual(self.group_cards(), [])

    def test_db_down_card_once(self):
        self.states[CHAT] = ("completed", dict(UA_DATA))
        self.press(None); self.press(None)
        self.assertEqual(len(self.group_cards()), 1)
        self.assertIn("БД недоступна", self.group_cards()[0])

    def test_db_idempotent_sql(self):
        import inspect
        src = inspect.getsource(ndb.accept_terms)
        self.assertIn("terms_accepted_at IS NULL", src)
        import docubridge_db_pool_a as pa
        self.assertIn("ADD COLUMN IF NOT EXISTS terms_accepted_at", inspect.getsource(pa))


class TestAddressText(Base):
    def test_after_order_offers_button(self):
        self.states[CHAT] = ("completed", dict(UA_DATA))
        self.say("какой у вас адрес?")
        self.assertIn("Адрес для отправки документов пришлёт менеджер после того, как вы подтвердите условия",
                      self.client_msgs()[-1])
        self.assertEqual(cbs(self.client_kb()), ["tc:DB7777"])
        self.assertEqual(self.group_cards(), [])
        self.ai_m.assert_not_called()

    def test_before_order(self):
        self.states[CHAT] = ("greeting", {})
        self.say("куда отправлять документы?")
        self.assertIn("менеджер даёт после оформления заявки и подтверждения условий", self.client_msgs()[-1])
        self.assertIn("/start", self.client_msgs()[-1])

    def test_on_step_keeps_step(self):
        self.to_quote(); self.say(m.BTN_APPLY, "а адрес какой?")
        self.assertEqual(self.state(), "ask_from_city")
        self.assertIn("после оформления заявки", self.last()[0])

    def test_ukrainian(self):
        self.states[CHAT] = ("completed", dict(UA_DATA))
        self.say("яка у вас адреса?")
        self.assertIn("Адресу для відправлення документів надішле менеджер", self.client_msgs()[-1])
        self.assertEqual(labels(self.client_kb()), ["✅ Умови підходять, потрібна адреса"])


class TestHandoff24h(Base):
    def test_all_handoff_texts(self):
        for r in ("human", "discount", "attachment", "complaint"):
            self.assertIn("в течение 24 часов", ho.client_reply(r, "DB1"), r)
            self.assertIn("протягом 24 годин", ho.client_reply(r, "DB1", "uk"), r)
        self.assertIn("в течение 24 часов", ns.t("ho_question_menu", "ru", manager="@m", rt=ns.rt("ru")))
        fs.contact_manager(CHAT)
        self.assertIn("в течение 24 часов", self.client_msgs()[-1])

    def test_detect(self):
        for t_, r in (("хочу скидку", "discount"), ("а можно дешевле?", "discount"), ("дайте знижку", "discount"),
                      ("это жалоба, документы потеряли", "complaint"), ("поверніть гроші", "complaint"),
                      ("позовите менеджера", "human"), ("потрібна жива людина", "human")):
            self.assertEqual(ho.detect_handoff(t_), r, t_)
        for t_ in ("Киев", "паспорт можно?", "сколько стоит", "Доверенность", "/start", "Ірина Коваль"):
            self.assertIsNone(ho.detect_handoff(t_), t_)

    def test_discount_on_step_keeps_step_no_ai(self):
        self.to_quote(); self.say(m.BTN_APPLY, "а скидку дадите?")
        self.assertEqual(self.state(), "ask_from_city")
        self.assertIn("по тарифу: €140 за конверт", self.last()[0]); self.assertIn("в течение 24 часов", self.last()[0])
        self.fai_m.assert_not_called(); self.ai_m.assert_not_called()
        cards = self.group_cards()
        self.assertEqual(len(cards), 1); self.assertIn("просьба о скидке", cards[0])

    def test_throttle_30_min(self):
        self.say("хочу поговорить с человеком", "позовите менеджера", "это жалоба")
        self.assertEqual(len(self.group_cards()), 1)
        with mock.patch.object(ho.time, "time", return_value=ho.time.time() + 31 * 60):
            self.say("позовите менеджера")
        self.assertEqual(len(self.group_cards()), 2)

    def test_human_button(self):
        ho.next_steps_cb(cb("ns:human:DB7777"))
        self.assertIn("в течение 24 часов", self.client_msgs()[-1])
        self.assertIn("клиент просит человека", self.group_cards()[0])

    def test_unknown_question_goes_to_human(self):
        self.ai_m.return_value = "Сейчас умные ответы не знают ответа на этот вопрос."
        self.states[CHAT] = ("greeting", {})
        self.say("а у вас есть страховка?")
        self.assertIn("в течение 24 часов", self.client_msgs()[-1])
        self.assertTrue(any("вопрос, на который бот не ответил" in c for c in self.group_cards()))

    def test_photo(self):
        self.to_quote(); self.say(m.BTN_APPLY)
        ho.attachment_received(NS(chat=NS(id=CHAT, type="private"), content_type="photo", caption="скан",
                                  message_id=42, from_user=USER))
        self.assertEqual(self.state(), "ask_from_city")
        self.assertIn("только текст и кнопки", self.last()[0])
        self.fwd_m.assert_called_once_with(GROUP, CHAT, 42)

    def test_group_attachment_ignored(self):
        ho.attachment_received(NS(chat=NS(id=GROUP, type="group"), content_type="document", caption=None,
                                  message_id=1, from_user=USER))
        self.assertEqual(m.bot.send_message.call_count, 0)


class TestCurrency(Base):
    def choose(self, cur, stored=None, ticket="DB7777", requested=False):
        stored = dict(UA_DATA) if stored is None else stored
        with mock.patch.object(ndb, "set_pay_currency", return_value=stored) as sp, \
                mock.patch.object(ndb, "pay_requested", return_value=requested):
            ho.currency_cb(cb(f"pc:{ticket}:{cur}"))
        return sp

    def test_default_currencies_and_env(self):
        self.assertEqual(pcfg.DEFAULT_CURRENCIES, "UAH,EUR,RUB")
        self.assertEqual(pcfg.currencies(), ["UAH", "EUR", "RUB"])
        with mock.patch.dict(os.environ, {"DOCUBRIDGE_PAY_CURRENCIES": "eur, pln;bad1,EUR"}):
            self.assertEqual(pcfg.currencies(), ["EUR", "PLN"])

    def test_uah_before_payreq_no_details(self):
        self.states[CHAT] = ("completed", {"ticket": "DB7777"})
        sp = self.choose("UAH")
        sp.assert_called_once_with("DB7777", CHAT, "UAH")
        self.assertEqual(self.client_msgs()[-1], "Валюта UAH сохранена. Реквизиты пришлём в этом чате, "
                                                 "когда документы поступят в Братиславу.")
        self.assertNotIn("UA853220010000026002310087318", self.texts())
        self.assertNotIn("Отримувач", self.texts())
        self.assertEqual(self.states[CHAT], ("completed", {"ticket": "DB7777", "pay_currency": "UAH"}))
        self.assertEqual(self.group_cards(), ["💱 DB7777: клиент выбрал UAH"])

    def test_uah_after_payreq_details_verbatim(self):
        self.choose("UAH", requested=True)
        self.assertEqual(self.client_msgs()[-1],
                         "📥 Документы по заявке DB7777 поступили в Братиславу. Теперь нужна оплата — "
                         "100% предоплата.\n\nРеквизиты (UAH):\n" + UAH_TEXT)
        self.assertEqual(self.group_cards(), ["💱 DB7777: клиент выбрал UAH"])

    def test_ru_by_saved_text(self):
        self.choose("RUB", dict(RU_DATA))
        self.assertEqual(self.client_msgs()[-1], "Валюта RUB сохранена. Реквизиты пришлём в этом чате, "
                                                 "когда документы поступят нашему агенту в Москве.")
        self.assertEqual(self.group_cards(), ["💱 DB7777: клиент выбрал RUB"])

    def test_eur_no_details_alert_only_after_payreq(self):
        self.choose("EUR", dict(RU_DATA))
        self.assertNotIn("реквизитов нет", " ".join(self.group_cards()))
        self.choose("EUR", dict(RU_DATA), requested=True)
        self.assertIn("пришлёт менеджер отдельным сообщением в этом чате в течение 24 часов", self.client_msgs()[-1])
        self.assertIn("⚠️ DB7777: клиент выбрал EUR, реквизитов нет, пришлите вручную", self.group_cards())
        self.choose("EUR", dict(RU_DATA), requested=True)
        self.assertEqual(len(self.group_cards()), 2)          # each note once

    def test_db_down_on_requested_check_sends_no_details(self):
        with mock.patch.object(ndb, "set_pay_currency", return_value=dict(UA_DATA)), \
                mock.patch.object(ndb, "pay_requested", return_value=None):
            ho.currency_cb(cb("pc:DB7777:UAH"))
        self.assertNotIn("UA853220010000026002310087318", self.texts())

    def test_env_overrides_text(self):
        with mock.patch.dict(os.environ, {"DOCUBRIDGE_PAYMENT_TEXT_UAH": "Нові реквізити\\nрядок 2",
                                          "DOCUBRIDGE_PAYMENT_TEXT_EUR": "EUR реквизиты"}):
            self.choose("UAH", requested=True)
            self.assertIn("Нові реквізити\nрядок 2", self.client_msgs()[-1])
            self.assertNotIn("UA853220010000026002310087318", self.client_msgs()[-1])
            self.choose("EUR", requested=True)
            self.assertIn("EUR реквизиты", self.client_msgs()[-1])

    def test_stale_button_keeps_state(self):
        self.states[CHAT] = ("ask_name", {"from_city": "Киев"})
        sp = self.choose("USD")
        sp.assert_not_called()
        self.assertEqual(self.states[CHAT], ("ask_name", {"from_city": "Киев"}))
        self.assertIn("устарела", self.client_msgs()[-1])

    def test_foreign_order_keeps_state(self):
        self.states[CHAT] = ("ask_doc_type", {"x": 1})
        self.choose("UAH", stored=False, ticket="DB1111", requested=True)
        self.assertEqual(self.states[CHAT], ("ask_doc_type", {"x": 1}))
        self.assertNotIn("UA853220010000026002310087318", self.texts())

    def test_db_down_noted(self):
        with mock.patch.object(ndb, "set_pay_currency", return_value=None):
            ho.currency_cb(cb("pc:DB7777:EUR"))
        self.assertIn("БД недоступна", self.group_cards()[0])

    def ask(self, text, requested):
        with mock.patch.object(ndb, "pay_requested", return_value=requested) as pr:
            self.say(text)
        return pr

    def test_how_to_pay_before_payreq(self):
        self.states[CHAT] = ("completed", dict(UA_DATA))
        self.ask("куда платить?", False)
        self.assertIn("В какой валюте будете оплачивать заявку DB7777?", self.last()[0])
        self.states[CHAT] = ("completed", dict(UA_DATA, pay_currency="UAH"))
        pr = self.ask("как оплатить?", False)
        pr.assert_called_once_with("DB7777", CHAT)
        self.assertIn("Валюта UAH сохранена. Реквизиты пришлём в этом чате, когда документы поступят "
                      "в Братиславу.", self.last()[0])
        self.assertNotIn("UA853220010000026002310087318", self.texts())
        self.ask("как оплатить?", None)                         # DB down → still no details
        self.assertNotIn("UA853220010000026002310087318", self.texts())

    def test_how_to_pay_after_payreq(self):
        self.states[CHAT] = ("completed", dict(UA_DATA, pay_currency="UAH"))
        self.ask("как оплатить?", True)
        self.assertEqual(self.last()[0], "📥 Документы по заявке DB7777 поступили в Братиславу. Теперь нужна "
                                         "оплата — 100% предоплата.\n\nРеквизиты (UAH):\n" + UAH_TEXT)
        self.ai_m.assert_not_called()

    def test_payment_question_before_order(self):
        self.to_quote(); self.say(m.BTN_APPLY, "а как оплатить?")
        self.assertEqual(self.state(), "ask_from_city")
        self.assertIn("100% предоплата после поступления документов", self.last()[0])
        self.assertIn("реквизиты пришлём в этом чате только после того, как документы поступят", self.last()[0])
        self.assertNotIn("UA853220010000026002310087318", self.texts())
        self.fai_m.assert_not_called()


class TestPaymentRequest(Base):
    def run_req(self, row, marked=True):
        with mock.patch.object(ndb, "get_order_pay_info", return_value=row), \
                mock.patch.object(ndb, "mark_pay_requested", return_value=marked) as mp:
            res = adm.request_payment("DB7777")
        self.mark = mp
        return res

    def test_uah_from_ukraine(self):
        ok, msg = self.run_req({"ticket": "DB7777", "chat_id": CHAT, "payload": UA_DATA, "pay_currency": "UAH"})
        self.assertTrue(ok)
        self.assertEqual(msg, "✅ DB7777: клиенту отправлена просьба об оплате (валюта: UAH).")
        self.mark.assert_called_once_with("DB7777")
        t_ = self.client_msgs()[-1]
        self.assertTrue(t_.startswith("📥 Документы по заявке DB7777 поступили в Братиславу. "
                                      "Теперь нужна оплата — 100% предоплата."))
        self.assertIn(UAH_TEXT, t_)

    def test_mark_not_stored_warns_admin(self):
        ok, msg = self.run_req({"ticket": "DB7777", "chat_id": CHAT, "payload": UA_DATA, "pay_currency": "UAH"},
                               marked=None)
        self.assertTrue(ok); self.assertIn("не сохранена в БД", msg)

    def test_no_currency_asks_choice(self):
        ok, msg = self.run_req({"ticket": "DB7777", "chat_id": CHAT, "payload": RU_DATA, "pay_currency": None})
        self.assertIn("поступили нашему агенту в Москве", self.client_msgs()[-1])
        self.assertIn("Выберите валюту оплаты кнопкой ниже.", self.client_msgs()[-1])
        self.assertEqual(cbs(self.client_kb()), ["pc:DB7777:UAH", "pc:DB7777:EUR", "pc:DB7777:RUB"])
        self.assertIn("валюта не выбрана", msg)

    def test_eur_fallback_and_admin_note(self):
        ok, msg = self.run_req({"ticket": "DB7777", "chat_id": CHAT, "payload": RU_DATA, "pay_currency": "EUR"})
        self.assertIn("пришлёт менеджер отдельным сообщением в этом чате в течение 24 часов", self.client_msgs()[-1])
        self.assertIn("реквизитов нет, пришлите вручную", msg)

    def test_ukrainian_client(self):
        self.run_req({"ticket": "DB7777", "chat_id": CHAT, "payload": dict(UA_DATA, lang="uk"), "pay_currency": "UAH"})
        self.assertTrue(self.client_msgs()[-1].startswith("📥 Документи за заявкою DB7777 надійшли до Братислави."))

    def test_button_routes_to_request(self):
        with mock.patch.object(adm, "request_payment", return_value=(True, "ok")) as rp, \
                mock.patch.object(adm, "can_press_status", return_value=True):
            adm.admin_status_button(NS(id="1", data="st:DB7777:payreq", from_user=NS(id=1, username="a"),
                                       message=NS(chat=NS(id=GROUP, type="group"))))
        rp.assert_called_once_with("DB7777")

    def test_status_keyboard_has_button(self):
        import docubridge_keyboards as kbm
        kb = kbm.status_inline_keyboard("DB7777")
        self.assertIn("st:DB7777:payreq", cbs(kb))
        self.assertIn("📥 Документы получены — запросить оплату", labels(kb))

    def test_db_sql_idempotent(self):
        import inspect
        import docubridge_db_pool_a as pa
        self.assertIn("ADD COLUMN IF NOT EXISTS pay_requested_at", inspect.getsource(pa))
        self.assertIn("COALESCE(pay_requested_at, NOW())", inspect.getsource(ndb.mark_pay_requested))
        with mock.patch.object(ndb, "DB_URL", None):
            self.assertIsNone(ndb.mark_pay_requested("DB1"))
            self.assertIsNone(ndb.pay_requested("DB1", 1))


class TestStatusAndTrack(Base):
    def test_status_ru_ua(self):
        t_ = adm.client_status_message("DB7777", "departed_hub", "TRK1")
        self.assertIn("отправлено в хаб", t_); self.assertIn("TRK1", t_)
        self.assertIn("Следующий статус придёт сюда автоматически", t_)
        u = adm.client_status_message("DB7777", "departed_hub", "TRK1", "uk")
        self.assertEqual(u, "📦 Відправлення DB7777: новий статус — «відправлено до хабу».\n"
                            "Коментар / трек-номер перевізника: TRK1\n"
                            "Документи в дорозі. Наступний статус надійде сюди автоматично.\n"
                            "Детальніше: /track і номер DB7777")

    def test_status_uses_order_language(self):
        row = {"ticket": "DB7777", "chat_id": CHAT, "payload": {"lang": "uk"}, "status": "at_hub"}
        with mock.patch.object(adm, "get_shipment_by_ticket", return_value=None), \
                mock.patch.object(adm, "update_shipment_status", return_value=row), \
                mock.patch.object(adm, "update_status_async"):
            adm.apply_status_change("DB7777", "at_hub")
        self.assertIn("Документи на хабі", self.client_msgs()[-1])

    def test_track_hint_own_chat(self):
        row = {"ticket": "DB7777", "chat_id": CHAT, "status": "accepted", "created_at": None, "payload": UA_DATA}
        with mock.patch.object(fs, "get_shipment_by_ticket", return_value=row), \
                mock.patch.object(fs, "get_shipment_events", return_value=[]):
            ho.next_steps_cb(cb("ns:track:DB7777"))
            self.assertIn("Передача документов: отправьте документы своими силами", self.last()[0])
            self.assertIn("100% после поступления документов в Братиславу", self.last()[0])
            fs.handle_track_ticket(999, "DB7777")
            self.assertNotIn("Что дальше", self.last()[0])


class TestLanguage(Base):
    def test_detect(self):
        for t_, lang in (("Скільки коштує?", "uk"), ("як оплатити", "uk"), ("що це", "uk"), ("можна дешевше", "uk"),
                         ("яка у вас адреса?", "uk"), ("дайте знижку", "uk"),
                         ("сколько стоит", "ru"), ("Привет, можно?", "ru"), ("вам удобно?", "ru"),
                         ("будь готов", "ru"), ("де-факто", "ru"), ("ви", "ru"), ("коли", "ru"), ("будь ласка", "uk"),
                         ("какой адрес?", "ru"), ("Hello", "ru")):
            self.assertEqual(L.detect_lang(t_), lang, t_)
        for t_ in ("", "/start", "+380671234567", "123"):
            self.assertIsNone(L.detect_lang(t_), t_)

    def test_language_code_fallback(self):
        self.assertEqual(L.get_lang(1, NS(language_code="uk")), "uk")
        self.assertEqual(L.get_lang(1, NS(language_code="ru")), "ru")
        self.assertEqual(L.get_lang(1, None), "ru")
        L.remember(1, "сколько стоит")
        self.assertEqual(L.get_lang(1, NS(language_code="uk")), "ru")    # text beats language_code

    def test_buttons_and_data_steps_do_not_switch(self):
        L.remember(CHAT, "Скільки коштує?")
        self.to_quote()                                   # RU button labels
        self.say(m.BTN_APPLY, "Киев", "Минск")
        self.assertEqual(L.get_lang(CHAT), "uk")
        L.reset()
        self.states[CHAT] = ("ask_name", {})
        self.say("Ірина Коваль")
        self.assertEqual(L.get_lang(CHAT), "ru")          # a name says nothing about the language

    def test_ukrainian_order_messages(self):
        self.to_quote()
        self.say("Скільки коштує доставка?")               # UA question on the quote step
        self.say(m.BTN_APPLY, "Киев", "Минск", "✅ Ірина Коваль", "067 123 45 67")
        cs = self.confirm()
        self.assertEqual(cs.call_args.args[1]["lang"], "uk")         # stored on the order
        thanks, terms, curq = self.client_msgs()[-3:]
        self.assertTrue(thanks.startswith("✅ Заявку оформлено.\nНомер відправлення: DB7777"))
        self.assertIn("1. Передача документів: надішліть документи власними силами та за свій рахунок "
                      "на адресу нашої людини у Словаччині — Новою Поштою або іншим зручним способом.", thanks)
        self.assertIn("2. Оплата: 100% передоплата після того, як документи надійдуть до Братислави.", thanks)
        self.assertIn("Умови за заявкою DB7777:", terms)
        self.assertEqual(labels(self.client_kb(-2)), ["✅ Умови підходять, потрібна адреса"])
        self.assertEqual(curq, "У якій валюті оплачуватимете заявку DB7777? "
                               "Оплата — після того, як документи надійдуть до Братислави.")
        self.assertEqual(labels(self.client_kb())[3:], ["📨 Як передати документи", "📍 Відстежити", "🙋 Потрібна людина"])
        self.assertEqual(self.data()["lang"], "uk")

    def test_ukrainian_currency_and_terms(self):
        L.remember(CHAT, "дякую")
        with mock.patch.object(ndb, "set_pay_currency", return_value=dict(UA_DATA)), \
                mock.patch.object(ndb, "pay_requested", return_value=False):
            ho.currency_cb(cb("pc:DB7777:UAH"))
        self.assertEqual(self.client_msgs()[-1], "Валюту UAH збережено. Реквізити надішлемо в цьому чаті, "
                                                 "коли документи надійдуть до Братислави.")
        with mock.patch.object(ndb, "set_pay_currency", return_value=dict(RU_DATA)), \
                mock.patch.object(ndb, "pay_requested", return_value=False):
            ho.currency_cb(cb("pc:DB7777:RUB"))
        self.assertEqual(self.client_msgs()[-1], "Валюту RUB збережено. Реквізити надішлемо в цьому чаті, "
                                                 "коли документи надійдуть нашому агенту в Москві.")
        with mock.patch.object(ndb, "set_pay_currency", return_value=dict(RU_DATA)), \
                mock.patch.object(ndb, "pay_requested", return_value=True):
            ho.currency_cb(cb("pc:DB7777:EUR"))
        self.assertIn("Реквізити для оплати в EUR надішле менеджер окремим повідомленням у цьому чаті протягом 24 годин",
                      self.client_msgs()[-1])
        self.assertNotIn("UA853220010000026002310087318", self.texts())
        with mock.patch.object(ndb, "accept_terms", return_value={"first": True, "at": None, "payload": UA_DATA}):
            ho.terms_cb(cb("tc:DB7777"))
        self.assertEqual(self.client_msgs()[-1], "Дякуємо! Запит адреси передано менеджеру — "
                                                 "він надішле адресу в цьому чаті протягом 24 годин.")
        self.assertIn("Язык клиента: UA", self.group_cards()[-1])

    def test_ukrainian_handoff_and_language_code(self):
        self.states[CHAT] = ("greeting", {})
        self.say("дайте знижку")
        self.assertIn("Вартість рахується за тарифом", self.client_msgs()[-1])
        L.reset()
        ho.next_steps_cb(cb("ns:hand:DB7777", user=NS(id=CHAT, username="u", language_code="uk")))
        self.assertIn("📨 Передача документів за заявкою DB7777", self.client_msgs()[-1])

    def test_all_templates_have_both_languages(self):
        from docubridge_texts import T
        for k, v in T.items():
            self.assertEqual(set(v), {"ru", "uk"}, k)
            self.assertTrue(v["ru"] and v["uk"], k)


class TestAiPromptAndDdl(unittest.TestCase):
    def test_prompt(self):
        p = rt.AI_SYSTEM_PROMPT
        for w in ("€140", "500 г", "10–14", "паспорта", "Скидок", "по-украински", "Братиславу",
                  "Москве", "Адрес даёт менеджер", "только после поступления документов", rt.AI_UNKNOWN_MARK):
            self.assertIn(w, p)

    def test_unknown_mark_and_temperature(self):
        fake = mock.MagicMock()
        fake.chat.completions.create.return_value = NS(choices=[NS(message=NS(content="[менеджер]"))])
        with mock.patch.object(rt, "client", fake):
            out = rt.ai_reply("какие законы?")
        self.assertTrue(out.lower().startswith(rt.AI_FAIL_PREFIXES))
        self.assertLessEqual(fake.chat.completions.create.call_args.kwargs["temperature"], 0.2)

    def test_ddl(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS handoff_log", ndb.NOTIFY_DDL)
        with mock.patch.object(ndb, "DB_URL", None):
            self.assertIsNone(ndb.claim_handoff(1, "client", "human", None, 30))
            self.assertIsNone(ndb.accept_terms("DB1", 1))
            self.assertIsNone(ndb.set_pay_currency("DB1", 1, "UAH"))


if __name__ == "__main__":
    unittest.main()
