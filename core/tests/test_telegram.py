import json
import threading
import time

from helpers import BrainTest
from test_dashboard import FAKE

from jarvis import dashboard, telegram


class BridgeCase(BrainTest):
    """A dashboard server with its phone bridge. The Bot API is faked."""

    def setUp(self):
        super().setUp()
        self.init()
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE}), encoding="utf-8")
        telegram.save(bot_token="123456789:AA" + "x" * 33)
        self.sent = []
        self._real_call = telegram.call
        telegram.call = self.fake_call
        self.server = dashboard.make_server(port=0)
        self.bridge = self.server.telegram

    def tearDown(self):
        telegram.call = self._real_call
        self.bridge.stop()
        self.server.runner.close()
        self.server.server_close()
        super().tearDown()

    def fake_call(self, method, params=None, file=None, timeout=70):
        self.sent.append((method, dict(params or {})))
        return {"message_id": 1}

    def texts(self, chat=None):
        return [p["text"] for m, p in self.sent if m == "sendMessage" and (chat is None or p["chat_id"] == chat)]

    def msg(self, chat, text):
        self.bridge._message({"chat": {"id": chat}, "text": text})



class TelegramBridgeTest(BridgeCase):
    """The phone bridge: pairing, owner-only, and relaying JARVIS's answer back."""

    def test_pairing_then_strangers_are_ignored(self):
        code = telegram.pairing_code()
        self.msg(111, "hello")
        self.assertIn("private assistant", self.texts(111)[0])
        self.msg(222, f"pair {code}")
        self.assertEqual(telegram.settings()["chat_id"], 222)
        self.assertIn("Paired", self.texts(222)[0])
        self.sent.clear()
        self.msg(111, "what's my budget?")  # not the owner: no answer, nothing reaches JARVIS
        self.assertEqual(self.sent, [])
        self.assertFalse(self.server.runner.busy)

    def test_owner_message_reaches_jarvis_and_answer_comes_back(self):
        telegram.save(chat_id=222)
        self.bridge.running = True
        threading.Thread(target=self.bridge._listen, daemon=True).start()
        time.sleep(0.2)
        self.msg(222, "how are the ads going?")
        deadline = time.time() + 15
        while time.time() < deadline and not any("On it." in t for t in self.texts(222)):
            time.sleep(0.1)
        answer = next(t for t in self.texts(222) if "On it." in t)
        self.assertIn(telegram.PREFIX.strip(), answer)  # JARVIS knew it came from the phone
        self.assertIn("how are the ads going?", answer)
        self.assertTrue(any("product-research agent is working" in t for t in self.texts(222)))

    def test_bot_token_is_redacted_from_the_journal(self):
        from jarvis import journal
        fake = "1234567890:AA" + "Fake_token-0" * 3  # pasted straight after a word, as owners do
        self.assertEqual(journal.redact(f"also{fake} thanks"), "also[redacted] thanks")


class PhoneControlTest(BridgeCase):
    """Approve buttons, the button keyboard, Today, and alerts."""

    def setUp(self):
        super().setUp()
        telegram.save(chat_id=222)

    def approval(self):
        from jarvis import db, records
        conn = db.connect()
        try:
            aid = records.approval_request(conn, "budget", "Raise the eye patch ad set to A$30/day", amount=30,
                                           currency="AUD")
            return aid, conn.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone()
        finally:
            conn.close()

    def test_approval_arrives_with_buttons(self):
        aid, row = self.approval()
        telegram.push_approval(row)
        method, params = self.sent[-1]
        self.assertIn("A$30/day", params["text"])
        self.assertIn("30 AUD", params["text"])
        datas = [b["callback_data"] for b in params["reply_markup"]["inline_keyboard"][0]]
        self.assertEqual(datas, [f"ap:{aid}:y", f"ap:{aid}:n"])

    def test_tapping_approve_records_it_clears_jarvis_and_starts_the_work(self):
        from jarvis import db, hooks, records
        aid, _ = self.approval()
        self.assertEqual(hooks.guard({"tool_name": "mcp__claude_ai_Meta__ads_update_entity"})
                         ["hookSpecificOutput"]["permissionDecision"], "ask")
        self.bridge._button({"id": "cb1", "data": f"ap:{aid}:y",
                             "message": {"chat": {"id": 222}, "message_id": 9, "text": "Approval needed"}})
        conn = db.connect()
        try:
            self.assertEqual(conn.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()[0], "approved")
        finally:
            conn.close()
        self.assertEqual(records.grant_active(), aid)
        self.assertEqual(hooks.guard({"tool_name": "mcp__claude_ai_Meta__ads_update_entity"})
                         ["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertTrue(self.server.runner.busy)  # JARVIS was told to carry it out
        self.assertIn("Carry it out now", self.server.runner.live["request"])
        edited = next(p for m, p in self.sent if m == "editMessageText")
        self.assertIn("Approved", edited["text"])

    def test_rejecting_opens_nothing(self):
        from jarvis import records
        aid, _ = self.approval()
        self.bridge._button({"id": "cb1", "data": f"ap:{aid}:n", "message": {"chat": {"id": 222}, "message_id": 9}})
        self.assertIsNone(records.grant_active())
        self.assertFalse(self.server.runner.busy)

    def test_strangers_cannot_press_buttons(self):
        aid, _ = self.approval()
        self.bridge._button({"id": "cb1", "data": f"ap:{aid}:y", "message": {"chat": {"id": 111}, "message_id": 9}})
        self.assertEqual(self.sent, [])

    def test_today_answers_from_the_brain_without_claude(self):
        self.approval()
        self.msg(222, "📋 Today")
        text = self.texts(222)[-1]
        self.assertIn("A$30/day", text)
        self.assertIn("No test running", text)
        self.assertFalse(self.server.runner.busy)

    def test_buttons_start_the_one_tap_actions(self):
        self.msg(222, "🎙 Brief me")
        self.assertIn(telegram.ACTIONS["brief"], self.server.runner.live["request"])

    def test_new_product_takes_the_next_message(self):
        self.msg(222, "➕ New product")
        self.assertFalse(self.server.runner.busy)
        self.msg(222, "https://example.com/neck-massager")
        self.assertIn(telegram.ACTIONS["product"] + "https://example.com/neck-massager", self.server.runner.live["request"])

    def test_urgent_alert_says_when_it_cannot_call(self):
        sent = telegram.alert("Kill rule hit on X1", urgent=True)
        self.assertEqual(sent[0], "message")
        self.assertTrue(sent[-1].startswith("no call"))
        self.assertIn("URGENT", self.texts(222)[-1])

    def test_owner_username_is_saved_for_calls(self):
        self.bridge._message({"chat": {"id": 222}, "from": {"username": "marley"}, "text": "/today"})
        self.assertEqual(telegram.settings()["call_user"], "marley")
