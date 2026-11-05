# -*- coding: utf-8 -*-
"""Funnel UX: offer first, buttons, price before cities, question guard, phones/contact."""
import unittest
from types import SimpleNamespace

from test_ads_start_help import Base, msg, CHAT, m, h


def kb_raw(kb):
    out = []
    for row in kb.keyboard:
        for b in row:
            out.append(b if isinstance(b, dict) else b.to_dict())
    return out


def kb_texts(kb):
    return [b["text"] for b in kb_raw(kb)] if kb is not None else []


class TestOfferFirst(Base):
    def test_all_route_deeplinks_offer_first(self):
        for key in ("ua_ru", "ua_by", "ru_ua", "by_ua"):
            self.sent.clear()
            h.start(msg(f"/start {key}"))
            text, kb = self.sent[0]
            self.assertEqual(len(self.sent), 1)
            for w in ("от €140", "10–14", "паспорта", "ID-карты", "военные билеты"):
                self.assertIn(w, text)
            self.assertIn(m.BTN_CALC, kb_texts(kb))
            self.assertIn(m.MENU_ALLOWED, kb_texts(kb))
            self.assertEqual(self.states[CHAT][0], "route_offer")

    def test_allowed_docs_keeps_offer(self):
        h.start(msg("/start ua_by")); self.sent.clear()
        h.any_text(msg(m.MENU_ALLOWED))
        self.assertIn("НЕ принимаем", self.texts())
        self.assertIn(m.BTN_CALC, kb_texts(self.sent[-1][1]))
        self.assertEqual(self.states[CHAT][0], "route_offer")

    def test_question_on_offer(self):
        h.start(msg("/start ua_ru")); self.sent.clear()
        h.any_text(msg("а сколько стоит?"))
        self.assertIn("€140", self.texts())
        self.assertEqual(self.states[CHAT][0], "route_offer")

    def test_menu_route_choice_offer(self):
        h.any_text(msg(m.MENU_SEND))
        h.any_text(msg(m.ROUTE_LABELS[0]))
        self.assertEqual(self.states[CHAT][0], "route_offer")

    def test_no_ru_by(self):
        self.assertIsNone(m.parse_hub_callback("hub:ru_by"))
        self.assertEqual(m.resolve_start_payload("ru_by")["kind"], "menu")


class TestButtons(Base):
    def test_doc_and_volume_keyboards(self):
        h.start(msg("/start ua_ru"))
        h.any_text(msg(m.BTN_CALC))
        self.assertEqual(self.states[CHAT][0], "ask_doc_type")
        self.assertIn(m.DOC_OTHER, kb_texts(self.sent[-1][1]))
        self.assertIn("Доверенность", kb_texts(self.sent[-1][1]))
        h.any_text(msg("Доверенность"))
        self.assertEqual(self.states[CHAT][0], "ask_volume")
        for p in ("до 100 г", "до 300 г", "до 500 г", "больше 500 г"):
            self.assertIn(p, kb_texts(self.sent[-1][1]))

    def test_doc_other_free_text(self):
        h.start(msg("/start ua_ru")); h.any_text(msg(m.BTN_CALC))
        h.any_text(msg(m.DOC_OTHER))
        self.assertEqual(self.states[CHAT][0], "ask_doc_type")
        h.any_text(msg("Апостиль на справку"))
        self.assertEqual(self.states[CHAT][1]["doc_type"], "Апостиль на справку")

    def test_passport_rejected_even_via_other(self):
        h.start(msg("/start ru_ua")); h.any_text(msg(m.BTN_CALC))
        h.any_text(msg(m.DOC_OTHER)); h.any_text(msg("загранпаспорт"))
        self.assertEqual(self.states[CHAT][0], "ask_doc_type")
        self.assertNotIn("doc_type", self.states[CHAT][1])

    def test_over_500_negotiable(self):
        h.start(msg("/start ua_ru"))
        for t in (m.BTN_CALC, "Свидетельство", "больше 500 г"):
            h.any_text(msg(t))
        self.assertIn("по согласованию", self.sent[-1][0])

    def test_free_text_volume_still_works(self):
        h.start(msg("/start ua_ru"))
        for t in (m.BTN_CALC, "Свидетельство", "40 листов"):
            h.any_text(msg(t))
        self.assertIn("€140", self.sent[-1][0])
        self.assertNotIn("€145", self.sent[-1][0])
        self.assertNotIn("срочн", self.sent[-1][0].lower())

    def test_offer_has_no_urgent_tariff(self):
        h.start(msg("/start ua_ru"))
        text = " ".join(x[0] for x in self.sent).lower()
        self.assertIn("€140", text)
        self.assertNotIn("€145", text)
        self.assertNotIn("срочно", text)


class TestPriceBeforeCities(Base):
    def go_to_quote(self):
        h.start(msg("/start ua_by"))
        for t in (m.BTN_CALC, "Диплом / аттестат", "до 300 г"):
            h.any_text(msg(t))

    def test_price_without_cities_then_order_card(self):
        self.go_to_quote()
        st, data = self.states[CHAT]
        self.assertEqual(st, "quote_shown")
        self.assertNotIn("from_city", data)
        q = self.sent[-1][0]
        self.assertIn("€140", q)
        self.assertIn("Украина → Беларусь", q)
        self.assertNotIn("None", q)
        h.any_text(msg(m.BTN_APPLY))
        self.assertEqual(self.states[CHAT][0], "ask_from_city")
        h.any_text(msg("Львов")); h.any_text(msg("Минск"))
        self.assertEqual(self.states[CHAT][0], "ask_name")
        h.any_text(msg("Иван Петров"))
        self.assertEqual(self.states[CHAT][0], "ask_phone")
        b = [x for x in kb_raw(self.sent[-1][1]) if x["text"] == m.BTN_SHARE_CONTACT][0]
        self.assertTrue(b.get("request_contact"))
        h.any_text(msg("+36 30 123 4567"))
        st, data = self.states[CHAT]
        self.assertEqual(st, "confirm")
        self.assertEqual(data["phone"], "+36301234567")
        self.assertIn("Львов", self.sent[-1][0]); self.assertIn("Минск", self.sent[-1][0])

    def test_question_not_saved_as_city(self):
        self.go_to_quote(); h.any_text(msg(m.BTN_APPLY)); self.sent.clear()
        for q in ("Сколько стоит?", "а цена какая", "стоимость доставки"):
            h.any_text(msg(q))
            st, data = self.states[CHAT]
            self.assertEqual(st, "ask_from_city")
            self.assertNotIn("from_city", data)
        self.assertIn("€140", self.texts())
        self.assertIn("Из какого города", self.sent[-1][0])
        h.any_text(msg("Киев")); h.any_text(msg("сколько дней идёт?"))
        self.assertEqual(self.states[CHAT][0], "ask_to_city")

    def test_contact_share(self):
        self.go_to_quote()
        for t in (m.BTN_APPLY, "Киев", "Минск", "Иван"):
            h.any_text(msg(t))
        c = msg(None); c.contact = SimpleNamespace(phone_number="421901234567")
        h.contact_shared(c)
        st, data = self.states[CHAT]
        self.assertEqual(st, "confirm"); self.assertEqual(data["phone"], "+421901234567")

    def test_contact_outside_phone_step(self):
        h.start(msg("/start")); self.sent.clear()
        c = msg(None); c.contact = SimpleNamespace(phone_number="+380501112233")
        h.contact_shared(c)
        self.assertEqual(self.texts(), m.FALLBACK_TEXT)


class TestPhone(unittest.TestCase):
    def test_valid(self):
        for p in ("+380501234567", "+7 912 345-67-89", "+375291234567", "+36301234567",
                  "+421 901 234 567", "+43 664 1234567", "0036301234567", "+49 (151) 2345678"):
            self.assertTrue(m.valid_phone(p), p)

    def test_invalid(self):
        for p in ("0501234567", "+12", "abc", "", "+0123456789", "+38050123456789012"):
            self.assertFalse(m.valid_phone(p), p)

    def test_step_question(self):
        self.assertTrue(m.is_step_question("Киев?"))
        self.assertTrue(m.is_step_question("какая цена"))
        self.assertFalse(m.is_step_question("Киев"))
        self.assertFalse(m.is_step_question("Стокгольм"))


if __name__ == "__main__":
    unittest.main()
