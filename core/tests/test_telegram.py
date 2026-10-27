import json
import threading
import time

from helpers import BrainTest
from test_dashboard import FAKE

from jarvis import dashboard, telegram


class TelegramBridgeTest(BrainTest):
    """The phone bridge: pairing, owner-only, and relaying JARVIS's answer back. The Bot API is faked."""

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
