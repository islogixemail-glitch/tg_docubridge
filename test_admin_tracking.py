# -*- coding: utf-8 -*-
"""Admin commands, tracking, Sheets export, create_shipment failure — mocked."""
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

os.environ["DOCUBRIDGE_SKIP_STARTUP"] = "1"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST_TOKEN_FOR_UNIT_TESTS")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as m  # noqa: E402
import docubridge_admin as adm  # noqa: E402
import docubridge_flow_dialog_post as post  # noqa: E402
import docubridge_sheets as sh  # noqa: E402
import docubridge_keyboards as kbm  # noqa: E402

ADMIN = 777
ROW = {
    "ticket": "DB1234",
    "chat_id": 555,
    "payload": {"from_country": "Украина", "to_country": "Россия", "name": "Иван",
                "phone": "+380501112233", "role": "sender", "weight_grams": 300,
                "urgency": "ordinary"},
    "status": "accepted",
    "created_at": datetime(2026, 10, 20, 10, 0),
    "updated_at": datetime(2026, 10, 21, 12, 0),
}


def msg(text, chat_id=ADMIN):
    return SimpleNamespace(text=text, chat=SimpleNamespace(id=chat_id))


class AdminBase(unittest.TestCase):
    def setUp(self):
        self.p_admin = mock.patch.object(adm, "ADMIN_CHAT_ID", ADMIN)
        self.p_admin.start()
        self.send = mock.patch.object(m.bot, "send_message").start()
        self.sheet = mock.patch.object(adm, "update_status_async").start()

    def tearDown(self):
        mock.patch.stopall()


class TestAdminAccess(AdminBase):
    def test_is_admin(self):
        self.assertTrue(adm.is_admin(ADMIN))
        self.assertFalse(adm.is_admin(555))

    def test_no_admin_env(self):
        with mock.patch.object(adm, "ADMIN_CHAT_ID", 0):
            self.assertFalse(adm.is_admin(0))


class TestOrders(AdminBase):
    def test_orders_list(self):
        with mock.patch.object(adm, "list_recent_shipments", return_value=[ROW]) as ls:
            adm.admin_orders(msg("/orders"))
        ls.assert_called_once_with(20)
        text = self.send.call_args[0][1]
        for part in ("DB1234", "20.10.2026", "Украина", "Россия", "Иван", "+380501112233", "принято"):
            self.assertIn(part, text)

    def test_orders_empty(self):
        with mock.patch.object(adm, "list_recent_shipments", return_value=[]):
            adm.admin_orders(msg("/orders"))
        self.assertIn("нет", self.send.call_args[0][1])

    def test_order_card(self):
        events = [{"status": "accepted", "comment": None, "created_at": datetime(2026, 10, 20, 10, 0)}]
        with mock.patch.object(adm, "get_shipment_by_ticket", return_value=ROW), \
             mock.patch.object(adm, "get_shipment_events", return_value=events):
            adm.admin_order(msg("/order db1234"))
        text = self.send.call_args[0][1]
        self.assertIn("Заявка DB1234", text)
        self.assertIn("€140", text)
        self.assertIn("Chat ID: 555", text)
        self.assertIsNotNone(self.send.call_args[1]["reply_markup"])

    def test_order_not_found(self):
        with mock.patch.object(adm, "get_shipment_by_ticket", return_value=None):
            adm.admin_order(msg("/order DB9999"))
        self.assertIn("не найдена", self.send.call_args[0][1])


class TestStatusChange(AdminBase):
    def test_status_command_updates_and_notifies_client(self):
        upd = dict(ROW, status="at_hub")
        with mock.patch.object(adm, "update_shipment_status", return_value=upd) as us:
            adm.admin_status(msg("/status DB1234 at_hub CDEK 123456"))
        us.assert_called_once_with("DB1234", "at_hub", "CDEK 123456")
        client_call = self.send.call_args_list[0]
        self.assertEqual(client_call[0][0], 555)
        self.assertIn("на хабе", client_call[0][1])
        self.assertIn("CDEK 123456", client_call[0][1])
        self.assertIn("клиент уведомлён", self.send.call_args_list[-1][0][1])
        self.sheet.assert_called_once_with("DB1234", "на хабе", "CDEK 123456")

    def test_status_unknown(self):
        with mock.patch.object(adm, "update_shipment_status") as us:
            adm.admin_status(msg("/status DB1234 lost"))
        us.assert_not_called()
        self.assertIn("Неизвестный статус", self.send.call_args[0][1])

    def test_status_db_fail(self):
        with mock.patch.object(adm, "update_shipment_status", return_value=None):
            ok, text = adm.apply_status_change("DB1234", "delivered")
        self.assertFalse(ok)
        self.send.assert_not_called()

    def test_inline_button(self):
        upd = dict(ROW, status="delivered")
        mock.patch.object(adm, "get_shipment_by_ticket", return_value=ROW).start()
        ans = mock.patch.object(m.bot, "answer_callback_query").start()
        call = SimpleNamespace(id="1", data="st:DB1234:delivered",
                               message=SimpleNamespace(chat=SimpleNamespace(id=ADMIN)),
                               from_user=SimpleNamespace(id=ADMIN))
        with mock.patch.object(adm, "update_shipment_status", return_value=upd) as us:
            adm.admin_status_button(call)
        us.assert_called_once_with("DB1234", "delivered", None)
        self.assertIn("доставлено", ans.call_args[0][1])

    def test_inline_button_non_admin(self):
        ans = mock.patch.object(m.bot, "answer_callback_query").start()
        call = SimpleNamespace(id="1", data="st:DB1234:delivered",
                               message=SimpleNamespace(chat=SimpleNamespace(id=555)),
                               from_user=SimpleNamespace(id=555))
        with mock.patch.object(adm, "update_shipment_status") as us:
            adm.admin_status_button(call)
        us.assert_not_called()
        self.assertIn("Нет доступа", ans.call_args[0][1])

    def test_inline_button_checks_from_user_not_chat(self):
        ans = mock.patch.object(m.bot, "answer_callback_query").start()
        # admin chat, but pressed by a non-admin member -> denied
        call = SimpleNamespace(id="1", data="st:DB1234:delivered",
                               message=SimpleNamespace(chat=SimpleNamespace(id=ADMIN)),
                               from_user=SimpleNamespace(id=555))
        with mock.patch.object(adm, "update_shipment_status") as us:
            adm.admin_status_button(call)
        us.assert_not_called()
        self.assertIn("Нет доступа", ans.call_args[0][1])

    def test_same_status_no_comment_is_noop(self):
        with mock.patch.object(adm, "get_shipment_by_ticket", return_value=ROW), \
             mock.patch.object(adm, "update_shipment_status") as us:
            ok, text = adm.apply_status_change("DB1234", "accepted")
        self.assertTrue(ok)
        self.assertIn("уже в этом статусе", text)
        us.assert_not_called()
        self.send.assert_not_called()
        self.sheet.assert_not_called()

    def test_same_status_legacy_key_is_noop(self):
        with mock.patch.object(adm, "get_shipment_by_ticket", return_value=dict(ROW, status="at hub")), \
             mock.patch.object(adm, "update_shipment_status") as us:
            ok, text = adm.apply_status_change("DB1234", "at_hub")
        us.assert_not_called()
        self.assertIn("уже в этом статусе", text)

    def test_same_status_with_comment_updates(self):
        upd = dict(ROW)
        with mock.patch.object(adm, "get_shipment_by_ticket", return_value=ROW), \
             mock.patch.object(adm, "update_shipment_status", return_value=upd) as us:
            ok, _ = adm.apply_status_change("DB1234", "accepted", "трек 99")
        us.assert_called_once_with("DB1234", "accepted", "трек 99")
        self.assertEqual(self.send.call_args_list[0][0][0], 555)

    def test_keyboard_buttons(self):
        kb = kbm.status_inline_keyboard("DB1234")
        data = [b.callback_data for row in kb.keyboard for b in row]
        self.assertEqual(data, [f"st:DB1234:{s}" for s in m.TRACK_STATUSES])
        for d in data:
            self.assertLessEqual(len(d.encode()), 64)

    def test_admin_lead_has_buttons(self):
        with mock.patch.object(kbm, "ADMIN_CHAT_ID", ADMIN):
            kbm.notify_admin_lead(555, dict(ROW["payload"], ticket="DB1234"))
        self.assertIsNotNone(self.send.call_args[1]["reply_markup"])


class TestTrack(unittest.TestCase):
    def test_track_dates(self):
        row = dict(ROW, status="at_hub")
        events = [
            {"status": "accepted", "comment": None, "created_at": datetime(2026, 10, 20, 10, 0)},
            {"status": "departed_hub", "comment": None, "created_at": datetime(2026, 10, 21, 9, 30)},
            {"status": "at_hub", "comment": "трек 42", "created_at": datetime(2026, 10, 22, 8, 15)},
        ]
        text = m.format_track_message(row, events)
        self.assertIn("✓ принято — 20.10.2026 10:00", text)
        self.assertIn("✓ отправлено в хаб — 21.10.2026 09:30", text)
        self.assertIn("✓ на хабе — 22.10.2026 08:15 (трек 42)", text)
        self.assertIn("· доставлено", text)

    def test_legacy_status(self):
        self.assertEqual(m.normalize_status("destination country"), "destination_country")
        self.assertEqual(m.normalize_status("на хабе"), "at_hub")
        self.assertIsNone(m.normalize_status("xxx"))


class TestCreateShipmentFailure(unittest.TestCase):
    def test_no_ticket_on_failure(self):
        data = dict(ROW["payload"])
        with mock.patch.object(post, "create_shipment", return_value=None), \
             mock.patch.object(post, "notify_admin_order_failed") as fail, \
             mock.patch.object(post, "notify_admin_lead") as lead, \
             mock.patch.object(post, "export_shipment_async") as exp, \
             mock.patch.object(post, "save_message"), \
             mock.patch.object(post, "set_state") as ss, \
             mock.patch.object(m.bot, "send_message") as send:
            post.handle_answer_post(555, post.BTN_CONFIRM, "confirm", data)
        fail.assert_called_once()
        lead.assert_not_called()
        exp.assert_not_called()
        ss.assert_not_called()
        text = send.call_args[0][1]
        self.assertNotIn("Номер отправления: DB", text)
        self.assertIn("Не удалось", text)
        self.assertNotIn("ticket", data)

    def test_success_exports(self):
        data = dict(ROW["payload"])
        with mock.patch.object(post, "create_shipment", return_value="DB5555"), \
             mock.patch.object(post, "notify_admin_lead") as lead, \
             mock.patch.object(post, "export_shipment_async", side_effect=RuntimeError("x")), \
             mock.patch.object(post, "save_message"), \
             mock.patch.object(post, "set_state"), \
             mock.patch.object(m.bot, "send_message") as send:
            post.handle_answer_post(555, post.BTN_CONFIRM, "confirm", data)
        lead.assert_called_once()
        self.assertIn("DB5555", send.call_args[0][1])

    def test_create_shipment_no_conn_returns_none(self):
        import docubridge_db_state as ds
        with mock.patch.object(ds, "DB_URL", "postgres://x"), \
             mock.patch.object(ds, "get_conn", return_value=None):
            self.assertIsNone(ds.create_shipment(1, {}))


class TestSheets(unittest.TestCase):
    def test_disabled_without_env(self):
        with mock.patch.dict(os.environ, {"GOOGLE_SA_JSON": "", "SHEET_ID": ""}):
            self.assertFalse(sh.sheets_enabled())
            self.assertIsNone(sh.export_shipment_async(1, {"ticket": "DB1"}))
            self.assertIsNone(sh.update_status_async("DB1", "принято"))

    def test_append_and_error_swallowed(self):
        ws = mock.MagicMock()
        with mock.patch.dict(os.environ, {"GOOGLE_SA_JSON": "{}", "SHEET_ID": "abc"}), \
             mock.patch.object(sh, "_get_worksheet", return_value=ws):
            t = sh.export_shipment_async(555, dict(ROW["payload"], ticket="DB1234"), 140)
            t.join(2)
        row = ws.append_row.call_args[0][0]
        self.assertEqual(row[0], "DB1234")
        self.assertEqual(row[10], 140)
        self.assertEqual(row[12], "'+380501112233")
        self.assertEqual(len(row), len(sh.SHEET_HEADER))
        with mock.patch.dict(os.environ, {"GOOGLE_SA_JSON": "{}", "SHEET_ID": "abc"}), \
             mock.patch.object(sh, "_get_worksheet", side_effect=Exception("boom")):
            t = sh.export_shipment_async(555, {"ticket": "DB1"})
            t.join(2)  # no exception propagates

    def test_update_status_cell(self):
        ws = mock.MagicMock()
        ws.find.return_value = SimpleNamespace(row=7)
        with mock.patch.dict(os.environ, {"GOOGLE_SA_JSON": "{}", "SHEET_ID": "abc"}), \
             mock.patch.object(sh, "_get_worksheet", return_value=ws):
            sh.update_status_async("DB1234", "доставлено", "ok").join(2)
        ws.update_cell.assert_any_call(7, sh.STATUS_COL, "доставлено")
        ws.update_cell.assert_any_call(7, sh.COMMENT_COL, "ok")


if __name__ == "__main__":
    unittest.main()
