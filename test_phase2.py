# -*- coding: utf-8 -*-
"""Phase 2 unit tests — pure helpers, no live BOT_TOKEN/DB required."""
import os
import re
import sys
import unittest

# Must set before importing main (avoids SystemExit / webhook)
os.environ["DOCUBRIDGE_SKIP_STARTUP"] = "1"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST_TOKEN_FOR_UNIT_TESTS")

# Ensure local import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as m  # noqa: E402


class TestRoutes(unittest.TestCase):
    def test_allowed_four(self):
        self.assertTrue(m.is_allowed_route("Украина", "Россия"))
        self.assertTrue(m.is_allowed_route("Украина", "Беларусь"))
        self.assertTrue(m.is_allowed_route("Россия", "Украина"))
        self.assertTrue(m.is_allowed_route("Беларусь", "Украина"))
        # normalize variants
        self.assertTrue(m.is_allowed_route("UA", "RU"))
        self.assertTrue(m.is_allowed_route("ukraine", "belarus"))

    def test_denied_ru_by_and_eu(self):
        self.assertFalse(m.is_allowed_route("Россия", "Беларусь"))
        self.assertFalse(m.is_allowed_route("Беларусь", "Россия"))
        self.assertFalse(m.is_allowed_route("Украина", "Польша"))
        self.assertFalse(m.is_allowed_route("Украина", "Germany"))
        self.assertFalse(m.is_allowed_route("Словакия", "Украина"))
        # same-country
        self.assertFalse(m.is_allowed_route("Украина", "Украина"))
        self.assertFalse(m.is_allowed_route("Россия", "Россия"))
        self.assertFalse(m.is_allowed_route("Беларусь", "Беларусь"))


class TestPricingEta(unittest.TestCase):
    def test_price_envelope_ordinary_95(self):
        self.assertEqual(m.base_price_eur(500, "обычная"), 95)
        self.assertEqual(m.base_price_eur(100, "обычная"), 95)
        self.assertEqual(m.base_price_eur(1, "обычная"), 95)
        q = m.compute_quote({
            "from_country": "Украина",
            "to_country": "Россия",
            "weight_grams": 500,
            "urgency": "обычная",
        })
        self.assertEqual(q["price_eur"], 95)

    def test_price_over_envelope_none(self):
        self.assertIsNone(m.base_price_eur(501, "обычная"))
        self.assertIsNone(m.base_price_eur(1000, "срочная"))

    def test_urgent_higher(self):
        urgent = m.base_price_eur(500, "срочная")
        self.assertIsNotNone(urgent)
        self.assertGreater(urgent, 95)

    def test_eta_10_14(self):
        eta = m.eta_working_days("Украина", "Россия")
        self.assertIsNotNone(eta)
        self.assertIn("10", eta)
        self.assertIn("14", eta)
        # style check: 10–14 or 10-14
        self.assertTrue(re.search(r"10\s*[–\-]\s*14", eta))
        self.assertEqual(m.eta_working_days("Россия", "Беларусь"), None)


class TestTicket(unittest.TestCase):
    def test_ticket_format(self):
        for _ in range(20):
            t = m.generate_ticket_candidate()
            self.assertRegex(t, r"^DB\d+$")
            self.assertTrue(m.is_valid_ticket_format(t))
        self.assertTrue(m.is_valid_ticket_format("DB1001"))
        self.assertTrue(m.is_valid_ticket_format("DB0001"))
        self.assertFalse(m.is_valid_ticket_format("DB"))
        self.assertFalse(m.is_valid_ticket_format("DB12"))
        self.assertFalse(m.is_valid_ticket_format("XX1001"))
        self.assertEqual(m.format_ticket_number(42), "DB0042")


class TestTrackStatuses(unittest.TestCase):
    def test_ordered_pipeline(self):
        expected = [
            "accepted",
            "departed for hub",
            "at hub",
            "destination country",
            "delivered",
        ]
        self.assertEqual(m.TRACK_STATUSES, expected)
        # indices increase along pipeline
        for i, st in enumerate(expected):
            self.assertEqual(m.track_status_index(st), i)


class TestVolumeParse(unittest.TestCase):
    def test_grams_and_pages(self):
        pages, weight, err = m.parse_volume("300 г")
        self.assertIsNone(err)
        self.assertEqual(weight, 300)
        pages, weight, err = m.parse_volume("40 листов")
        self.assertIsNone(err)
        self.assertEqual(pages, 40)
        self.assertEqual(weight, 240)


if __name__ == "__main__":
    unittest.main(verbosity=2)
