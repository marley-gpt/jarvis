import json
import os
from unittest import mock

from helpers import BrainTest

from jarvis import dashboard, paths, phone, sync, telegram


class PhoneSwitchTest(BrainTest):
    """Owner, 2026-11-10: the laptop answers the phone when he wants it to, and never alongside the PC."""

    def setUp(self):
        super().setUp()
        self.init()
        os.environ.pop("JARVIS_ROLE", None)  # the shared note decides, not the test default
        self.env = mock.patch.dict(os.environ, {"JARVIS_MACHINE": "marley", "LOCALAPPDATA": str(self.home / "local")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.environ["JARVIS_ROLE"] = "primary"
        super().tearDown()

    def as_machine(self, name):
        os.environ["JARVIS_MACHINE"] = name

    def note(self, **data):
        (self.sync_dir / phone.NOTE).write_text(json.dumps(data), encoding="utf-8")

    def test_without_a_note_the_role_file_decides(self):
        self.assertIsNone(phone.answers_here())
        self.assertEqual(paths.machine_role(), "primary")
        paths.set_machine_role("standby")
        self.assertEqual(paths.machine_role(), "standby")

    def test_the_note_names_the_one_machine_that_answers(self):
        paths.set_machine_role("standby")  # the note wins over the role file
        self.note(holder="Marleyslaptop", machines=["marley", "Marleyslaptop"])
        self.as_machine("Marleyslaptop")
        self.assertEqual(paths.machine_role(), "primary")
        self.as_machine("marley")
        self.assertEqual(paths.machine_role(), "standby")

    def test_laptop_and_desktop_resolve_to_known_machines(self):
        self.note(holder="marley", machines=["marley", "Marleyslaptop"])
        self.assertEqual(phone.resolve("laptop"), "Marleyslaptop")
        self.assertEqual(phone.resolve("desktop"), "marley")
        self.assertEqual(phone.resolve("here"), "marley")
        with self.assertRaises(phone.PhoneError):
            phone.resolve("fridge")

    def test_handing_over_pushes_the_brain_first_then_names_the_laptop(self):
        self.note(holder="marley", machines=["marley", "Marleyslaptop"])
        result = phone.hand_to(self.conn, "laptop")
        self.assertTrue(result["changed"])
        self.assertTrue((self.sync_dir / sync.BRAIN_ZIP).exists())
        self.assertEqual(phone.holder(), "Marleyslaptop")
        self.assertEqual(phone.state()["by"], "marley")
        self.assertEqual(paths.machine_role(), "standby")  # the PC stepped back the moment it handed over

    def test_a_standby_cannot_grab_the_phone_without_takeover(self):
        self.note(holder="marley", machines=["marley", "Marleyslaptop"])
        self.as_machine("Marleyslaptop")
        with self.assertRaises(phone.PhoneError):
            phone.hand_to(self.conn, "here")
        self.assertFalse(phone.hand_to(self.conn, "desktop")["changed"])  # the PC already answers: nothing to do
        phone.hand_to(self.conn, "here", takeover=True)
        self.assertEqual(phone.holder(), "Marleyslaptop")
        self.assertTrue(phone.state()["takeover"])

    def test_cli_status_and_switch(self):
        self.note(holder="marley", machines=["marley", "Marleyslaptop"])
        self.assertIn("marley answers", self.run_cli("phone").stdout)
        self.assertIn("Marleyslaptop answers the phone now", self.run_cli("phone", "laptop").stdout)
        self.assertIn("standby", self.run_cli("machine").stdout)  # the PC stepped back

    def test_poll_leaves_messages_unread_once_handed_away(self):
        with mock.patch.object(telegram, "settings", return_value={"bot_token": "t", "chat_id": 1}):
            bridge = telegram.Bridge(hub=None, runner=mock.Mock(busy=False), kokoro=None)
            self.note(holder="Marleyslaptop", machines=["marley", "Marleyslaptop"])  # handed away
            bridge.running = True
            calls = []

            def fake_call(method, params=None, timeout=None):
                calls.append((method, params))
                return [{"update_id": 7, "message": {"text": "hi"}}]

            with mock.patch.object(telegram, "call", side_effect=fake_call), \
                    mock.patch.object(bridge, "_message") as handled:
                bridge._poll()
            handled.assert_not_called()  # the laptop answers it, not the PC
            self.assertFalse(bridge.running)
            self.assertEqual(len(calls), 1)  # no acknowledgement: update 7 stays unread for the laptop

    def test_follow_phone_pulls_then_starts_when_handed_the_phone(self):
        # The PC pushes and hands over; this laptop (standby until now) pulls that brain and starts the phone.
        self.note(holder="marley", machines=["marley", "Marleyslaptop"])
        phone.hand_to(self.conn, "laptop")
        self.note(holder="marley", machines=["marley", "Marleyslaptop"])  # as the laptop saw it a moment ago
        self.as_machine("Marleyslaptop")
        bridge = mock.Mock(running=False)
        bridge.enabled.side_effect = lambda: paths.machine_role() == "primary"
        sleeps = []

        def hand_over(seconds):  # the note arrives through OneDrive between two checks
            sleeps.append(seconds)
            self.note(holder="Marleyslaptop", by="marley", machines=["marley", "Marleyslaptop"])

        with mock.patch.object(dashboard.time, "sleep", side_effect=hand_over), \
                mock.patch.object(sync, "pull", wraps=sync.pull) as pulled:
            dashboard.follow_phone(bridge, every=0, rounds=2)
        bridge.start.assert_called_once()
        pulled.assert_called_once()
        self.assertEqual(len(sleeps), 1)
