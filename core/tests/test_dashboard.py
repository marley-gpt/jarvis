import base64
import http.client
import json
import sys
import threading
import time

from helpers import CORE, BrainTest

from jarvis import dashboard, db, records

FAKE = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_claude.py")]
FAKE_TTS = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_tts.py")]


class DashboardTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE}), encoding="utf-8")
        self.server = dashboard.make_server(port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.events = []
        self.hub_q = self.server.hub.subscribe()

    def tearDown(self):
        self.server.shutdown()
        self.server.runner.close()
        self.server.tts.stop()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-Jarvis-Token"] = self.server.token
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def wait_idle(self):
        deadline = time.time() + 15
        while time.time() < deadline:
            ev = self.hub_q.get(timeout=15)
            self.events.append(ev)
            if ev == {"type": "status", "status": "idle"}:
                return
        self.fail("runner never finished")

    def test_page_and_state(self):
        status, html = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), html)
        records.task_add(self.conn, "Research posture corrector", agent="product-research")
        status, raw = self.request("GET", "/api/state", token=False)
        state = json.loads(raw)
        names = [a["name"] for a in state["agents"]]
        self.assertEqual(names[0], "mentor-learning")
        self.assertEqual(len(names), 12)
        self.assertIn("store-qa", names)
        self.assertIn("accountant", names)
        pr = next(a for a in state["agents"] if a["name"] == "product-research")
        self.assertEqual(pr["status"], "queued")
        self.assertEqual(pr["now"], "Research posture corrector")
        self.assertTrue(state["jarvis"]["claude"])

    def test_prototype_pages_and_shared_client(self):
        for path in ("/console", "/office", "/chat", "/classic"):
            status, html = self.request("GET", path, token=False)
            self.assertEqual(status, 200, path)
            self.assertIn(self.server.token.encode(), html)
        status, js = self.request("GET", "/jarvis.js", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"window.J = J", js)
        self.assertEqual(self.request("GET", "/nope", token=False)[0], 404)

    def test_state_has_console_sections(self):
        records.product_add(self.conn, "Posture Corrector")
        parent = records.task_add(self.conn, "Launch posture corrector")
        records.task_add(self.conn, "Research it", agent="product-research", parent=parent)
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertEqual(state["counts"]["delegations"], 1)
        self.assertEqual(state["counts"]["open_tasks"], 2)
        self.assertEqual([p["slug"] for p in state["products"]], ["posture-corrector"])
        self.assertIn("by_service", state["cost"])
        pr = next(a for a in state["agents"] if a["name"] == "product-research")
        self.assertEqual((pr["open"], pr["runs"]), (1, 0))

    def test_memory_search_and_entry(self):
        rows = json.loads(self.request("GET", "/api/memory?q=approval", token=False)[1])
        self.assertTrue(rows)
        self.assertIn("snippet", rows[0])
        everything = json.loads(self.request("GET", "/api/memory", token=False)[1])
        self.assertGreaterEqual(len(everything), len(rows))
        status, raw = self.request("GET", f"/api/memory/{rows[0]['id']}", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["meta"]["id"], rows[0]["id"])
        self.assertEqual(self.request("GET", "/api/memory/M-NOPE", token=False)[0], 404)

    def test_approval_from_dashboard(self):
        aid = records.approval_request(self.conn, "spend", "Test budget A$100")
        self.assertEqual(self.request("POST", "/api/approval", {"id": aid, "approve": True}, token=False)[0], 403)
        status, raw = self.request("POST", "/api/approval", {"id": f"A{aid}", "approve": True})
        self.assertEqual((status, json.loads(raw)["status"]), (200, "approved"))
        self.assertEqual(self.request("POST", "/api/approval", {"id": aid, "approve": False})[0], 400)
        self.assertEqual(self.request("POST", "/api/approval", {"id": 999, "approve": True})[0], 400)

    def test_requests_need_token_and_loopback_host(self):
        self.assertEqual(self.request("POST", "/api/chat", {"text": "hi"}, token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:80")[0], 403)
        self.assertEqual(self.request("POST", "/api/chat", {"text": "  "})[0], 400)

    def test_conversation_streams_delegation_and_resumes(self):
        self.assertEqual(self.request("POST", "/api/chat", {"text": "research the posture corrector"})[0], 202)
        self.wait_idle()
        kinds = [e["type"] for e in self.events]
        for kind in ("say", "dispatch", "agent_activity", "agent_done", "reply"):
            self.assertIn(kind, kinds)
        dispatch = next(e for e in self.events if e["type"] == "dispatch")
        self.assertEqual(dispatch["agent"], "product-research")
        self.assertEqual(dispatch["description"], "Research posture corrector")
        activity = next(e for e in self.events if e["type"] == "agent_activity")
        self.assertIn("posture corrector reviews", activity["line"])
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=False", say["text"])
        conn = db.connect()
        self.assertEqual(db.get_meta(conn, "dashboard_session"), "sess-123")
        conn.close()

        # The next message goes to the same running process: no start-up wait.
        self.events.clear()
        self.request("POST", "/api/chat", {"text": "and the angles?"})
        self.wait_idle()
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=False. Turn=2", say["text"])

        # After a stop the process is gone; the next message resumes the saved conversation.
        self.assertEqual(self.request("POST", "/api/stop", {})[0], 200)
        self.events.clear()
        self.request("POST", "/api/chat", {"text": "carry on"})
        self.wait_idle()
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=True. Turn=1", say["text"])

        # New conversation forgets the session id (memory and journal stay).
        self.assertEqual(self.request("POST", "/api/new", {})[0], 200)
        conn = db.connect()
        self.assertFalse(db.get_meta(conn, "dashboard_session"))
        conn.close()

    def test_reply_is_spoken_sentence_by_sentence(self):
        self.use_fake("--fake-stream")
        self.request("POST", "/api/chat", {"text": "start the research"})
        self.wait_idle()
        chunks = [e["text"] for e in self.events if e["type"] == "say_chunk"]
        self.assertEqual(chunks, ["On it, sir.", "The research team is starting now.", "Shall I brief the creative team too?"])
        say = next(e for e in self.events if e["type"] == "say")
        self.assertTrue(say["streamed"])  # shown in full, but not spoken twice

    def test_pasted_images_reach_jarvis_and_are_saved(self):
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).decode()
        status, _ = self.request("POST", "/api/chat", {"text": "", "images": [{"media_type": "image/png", "data": png}]})
        self.assertEqual(status, 202)
        self.wait_idle()
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Images=1", say["text"])
        self.assertIn("files/uploads/", say["text"].replace("\\", "/"))  # the path, so agents can open it
        self.assertEqual(len(list((self.home / "files" / "uploads").glob("*.png"))), 1)
        bad = {"text": "x", "images": [{"media_type": "image/svg+xml", "data": png}]}
        self.assertEqual(self.request("POST", "/api/chat", bad)[0], 400)

    def use_fake(self, *flags):
        (self.home / "config.json").write_text(json.dumps({"claude_command": [*FAKE, *flags]}), encoding="utf-8")

    def test_logged_out_cli_is_explained_once(self):
        self.use_fake("--fake-logged-out")
        self.request("POST", "/api/chat", {"text": "hello Jarvis"})
        self.wait_idle()
        self.assertFalse([e for e in self.events if e["type"] == "say"])  # the raw CLI message is not read out
        reply = next(e for e in self.events if e["type"] == "reply")
        self.assertTrue(reply["is_error"] and reply["login"])
        self.assertIn("isn't signed in", reply["text"])
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertTrue(state["jarvis"]["login_needed"])

        # Once signed in, the next answer clears the warning.
        self.use_fake()
        self.events.clear()
        self.request("POST", "/api/chat", {"text": "hello again"})
        self.wait_idle()
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertFalse(state["jarvis"]["login_needed"])

    def test_missing_saved_conversation_starts_fresh(self):
        db.set_meta(self.conn, "dashboard_session", "gone-456")
        self.use_fake("--fake-missing-session")
        self.request("POST", "/api/chat", {"text": "hello"})
        self.wait_idle()
        self.assertFalse([e for e in self.events if e["type"] == "error"])
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=False", say["text"])
        conn = db.connect()
        self.assertEqual(db.get_meta(conn, "dashboard_session"), "sess-123")
        conn.close()

    def test_kokoro_voice(self):
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE, "tts_command": FAKE_TTS}),
                                              encoding="utf-8")
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertTrue(state["tts"]["available"])
        self.assertEqual(state["tts"]["voice"], "jarvis")
        self.assertIn("bm_george", [v["id"] for v in state["tts"]["voices"]])
        self.assertIn("jarvis", [v["id"] for v in state["tts"]["voices"]])
        self.assertEqual(self.request("POST", "/api/tts", {"text": "Good evening."}, token=False)[0], 403)
        status, wav = self.request("POST", "/api/tts", {"text": "Good evening, sir.", "voice": "bm_lewis"})
        self.assertEqual(status, 200)
        self.assertEqual(wav[:4], b"RIFF")
        # Once the worker reports its voices, only those (and presets built from them) are offered.
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertEqual([v["id"] for v in state["tts"]["voices"]],
                         ["jarvis_crisp", "bm_george", "bm_lewis", "bm_daniel", "bf_emma"])
        status, wav = self.request("POST", "/api/tts", {"text": "Good evening, sir.", "voice": "jarvis_crisp"})
        self.assertEqual((status, wav[:4]), (200, b"RIFF"))
        self.assertEqual(self.request("POST", "/api/tts", {"text": "boom"})[0], 503)
        self.assertEqual(self.request("POST", "/api/tts", {"text": "  "})[0], 503)
        # A crashed worker is restarted on the next request.
        self.server.tts.proc.kill()
        self.server.tts.proc.wait()
        self.assertEqual(self.request("POST", "/api/tts", {"text": "Still here."})[0], 200)

    def test_voice_falls_back_without_kokoro(self):
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertFalse(state["tts"]["available"])
        self.assertEqual(self.request("POST", "/api/tts", {"text": "Hello"})[0], 503)

    def test_voice_prompt_is_jarvis_and_shell_safe(self):
        prompt = dashboard.VOICE_PROMPT
        for phrase in ("British", "sir", "approval", "who is doing what"):
            self.assertIn(phrase, prompt)
        self.assertFalse(set(prompt) & set('%^&|<>"'))

    def test_missing_cli_is_reported(self):
        (self.home / "config.json").write_text(json.dumps({"claude_command": ["definitely-not-a-real-cli-xyz"]}),
                                              encoding="utf-8")
        self.request("POST", "/api/chat", {"text": "hello"})
        self.wait_idle()
        err = next(e for e in self.events if e["type"] == "error")
        self.assertIn("Could not start Claude Code", err["text"])


class WakeWordTest(BrainTest):
    def test_hey_jarvis_reaches_the_dashboard_and_can_be_switched_off(self):
        self.init()
        fake = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_wake.py")]
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE, "wake_command": fake}), encoding="utf-8")
        server = dashboard.make_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        q = server.hub.subscribe()
        try:
            server.wake.start()
            ev = q.get(timeout=10)
            self.assertEqual(ev["type"], "wake")
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("POST", "/api/wake", json.dumps({"on": False}),
                         {"Host": f"127.0.0.1:{server.server_address[1]}", "X-Jarvis-Token": server.token})
            data = json.loads(conn.getresponse().read())
            conn.close()
            self.assertFalse(data["enabled"])
            self.assertFalse(data["on"])
            self.assertFalse(json.loads((self.home / "config.json").read_text(encoding="utf-8"))["wake_word"] is not False)
        finally:
            server.wake.stop()
            server.shutdown()
            server.server_close()
