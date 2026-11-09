import base64
import http.client
import io
import json
import queue
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import CORE, BrainTest

from jarvis import call, paths, quick

FAKE_STT = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_stt.py")]
FAKE_QUICK = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_quick.py")]


def speech() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x10\x00" * 16000)
    return buf.getvalue()


class FakeDashboard:
    """The parts of the dashboard a call uses: /api/chat, /api/tts and the live event stream."""

    def __init__(self, busy_for: int = 0):
        self.asked, self.events, self.busy_for = [], queue.Queue(), busy_for
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/chat":
                    if fake.busy_for > 0:
                        fake.busy_for -= 1
                        return self._send(409, b'{"error": "JARVIS is still answering."}', "application/json")
                    fake.asked.append(body["text"])
                    fake.events.put({"type": "say_chunk", "text": "Four brushes, sir."})
                    fake.events.put({"type": "say", "text": "Four brushes, sir.", "streamed": True})
                    return self._send(202, b"{}", "application/json")
                if self.path == "/api/tts":
                    return self._send(200, b"RIFF" + body["text"].encode(), "audio/wav")

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while True:
                        try:
                            ev = fake.events.get(timeout=0.5)
                            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except OSError:
                    pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class CallTest(BrainTest):
    busy_for = 0
    quick = False  # True: the quick model answers (a fake one); False: every turn goes to the main session

    def setUp(self):
        super().setUp()
        self.init()
        if self.quick:
            paths.set_config("call_quick_command", FAKE_QUICK)
        else:
            paths.set_config("call_quick_model", "off")
        self.dash = FakeDashboard(self.busy_for)
        self.server = call.make_server(port=0, ears=call.Ears(FAKE_STT), jarvis=call.Jarvis(self.dash.url))
        self.port = self.server.server_address[1]
        self.server.call.start()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.key = self.server.key
        self.got = queue.Queue()

    def tearDown(self):
        self.server.shutdown()
        self.server.call.ears.stop()
        self.server.call.brain.stop()
        self.server.server_close()
        self.dash.close()
        super().tearDown()

    def request(self, method, path, body=b"", key=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body, {"X-Call-Key": self.key if key is None else key, "Content-Type": ctype})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def listen(self):
        """Open the call page's event stream, as the phone does."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", f"/api/call/events?k={self.key}")
        resp = conn.getresponse()

        def read():
            try:
                for raw in resp:
                    line = raw.decode().strip()
                    if line.startswith("data: "):
                        self.got.put(json.loads(line[6:]))
            except OSError:
                pass
        threading.Thread(target=read, daemon=True).start()
        self.assertEqual(self.got.get(timeout=5)["type"], "connected")
        while not self.server.call.live():
            time.sleep(0.01)

    def next(self, kind, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            ev = self.got.get(timeout=deadline - time.time())
            if ev["type"] == kind:
                return ev
        self.fail(f"no {kind} event")


class CallKeyTest(CallTest):
    def test_nothing_works_without_the_key(self):
        self.assertEqual(self.request("GET", "/call?k=wrong", key="")[0], 403)
        self.assertEqual(self.request("GET", "/call", key="")[0], 403)
        self.assertEqual(self.request("GET", "/api/call/events?k=", key="")[0], 403)
        self.assertEqual(self.request("POST", "/api/call/hear", speech(), key="nope", ctype="audio/wav")[0], 403)
        self.assertEqual(self.dash.asked, [])

    def test_the_page_opens_with_the_key_and_is_the_same_key_next_time(self):
        status, html = self.request("GET", f"/call?k={self.key}")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/call/hear", html)
        self.assertEqual(call.call_key(), self.key)


class ConversationTest(CallTest):
    def test_jarvis_answers_what_the_owner_said_in_his_voice(self):
        self.listen()
        self.assertEqual(self.request("POST", "/api/call/hello")[0], 200)
        hello = self.next("say")
        self.assertRegex(hello["text"], r"^(Morning|Afternoon|Evening|You're up late), sir\..*What can I do for you\?$")
        self.assertEqual(base64.b64decode(hello["wav"]), b"RIFF" + hello["text"].encode())
        self.assertEqual(self.request("POST", "/api/call/hear", speech(), ctype="audio/wav")[0], 202)
        self.assertEqual(self.next("heard")["text"], "what did we sell today")
        self.assertEqual(self.next("say")["text"], "Four brushes, sir.")
        self.assertEqual(self.dash.asked, [call.PREFIX + "what did we sell today"])
        time.sleep(0.5)
        self.assertTrue(all(ev["type"] != "say" for ev in list(self.got.queue)))  # spoken once, not chunk + say

    def test_a_ring_with_something_to_say_opens_the_call_with_it(self):
        (call.call_dir() / "greeting.txt").write_text("Sir, the brush has its first sale.", encoding="utf-8")
        self.listen()
        self.request("POST", "/api/call/hello")
        self.assertEqual(self.next("say")["text"], "Sir, the brush has its first sale.")
        self.assertFalse((call.call_dir() / "greeting.txt").exists())

    def test_talking_over_jarvis_drops_what_he_has_not_said_yet(self):
        self.listen()
        c = self.server.call
        c.lines.put((time.time() - 5, "An old sentence the owner talked over.", 0, "main"))
        c.hush()
        c.say("A new one.")
        self.assertEqual(self.next("say")["text"], "A new one.")

    def test_short_pieces_are_spoken_together_so_it_flows(self):
        self.listen()
        for piece in ("Hello,", "sir,", "I can hear you."):
            self.server.call.say(piece)
        self.assertEqual(self.next("say")["text"], "Hello, sir, I can hear you.")

    def test_trailing_off_on_and_waits_for_the_rest_of_the_thought(self):
        c = self.server.call
        c.WAIT_FOR_MORE = 0.5
        said = iter(["check the brush ads and", "tell me the spend"])
        c.ears.hear = lambda wav: next(said)
        first = threading.Thread(target=c.heard, args=(speech(),))
        first.start()
        time.sleep(0.1)
        c.heard(speech())
        first.join()
        self.assertEqual(self.dash.asked, [call.PREFIX + "check the brush ads and tell me the spend"])

    def test_after_being_talked_over_the_rest_of_that_reply_is_dropped(self):
        self.listen()
        c = self.server.call
        c.hush()
        self.dash.events.put({"type": "say_chunk", "text": "The rest of the old answer."})
        self.dash.events.put({"type": "status", "status": "idle"})
        self.dash.events.put({"type": "say_chunk", "text": "A fresh answer."})
        self.assertEqual(self.next("say")["text"], "A fresh answer.")

    def test_the_journal_keeps_only_the_owners_words(self):
        from jarvis import journal
        self.assertEqual(journal.owner_words(call.PREFIX + "what did we sell today").strip(), "what did we sell today")

    def test_not_audio_is_refused(self):
        self.assertEqual(self.request("POST", "/api/call/hear", b"hello", ctype="audio/wav")[0], 400)


class BusyTest(CallTest):
    busy_for = 2

    def test_a_busy_jarvis_gets_it_when_he_is_free(self):
        self.listen()
        self.request("POST", "/api/call/hear", speech(), ctype="audio/wav")
        self.assertEqual(self.next("say")["text"], "Four brushes, sir.")
        self.assertEqual(len(self.dash.asked), 1)


class QuickBrainTest(CallTest):
    quick = True

    def test_small_talk_is_answered_by_the_quick_model_without_the_main_session(self):
        self.listen()
        self.server.call.ears.hear = lambda wav: "how are you doing"
        status, body = self.request("POST", "/api/call/hear", speech(), ctype="audio/wav")
        self.assertEqual(status, 202)
        turn = json.loads(body)["turn"]
        said = self.next("say")
        self.assertEqual(said["turn"], turn)
        self.assertTrue(said["text"].startswith("Very good, sir."))
        time.sleep(0.5)
        self.assertEqual(self.dash.asked, [])

    def test_real_work_is_handed_to_the_main_session_and_its_answer_is_spoken(self):
        self.listen()
        self.request("POST", "/api/call/hear", speech(), ctype="audio/wav")
        self.assertEqual(self.next("say")["text"], "Let me check that, sir.")
        self.assertEqual(self.next("say")["text"], "Four brushes, sir.")
        self.assertEqual(len(self.dash.asked), 1)
        self.assertIn("Tell the owner what sold today. Then give the ad spend.", self.dash.asked[0])
        self.assertIn("what did we sell today", self.dash.asked[0])
        self.assertTrue(all("[[" not in ev.get("text", "") for ev in list(self.got.queue)))

    def test_quick_turns_are_journaled(self):
        from jarvis import journal
        self.server.call.ears.hear = lambda wav: "how are you doing"
        self.server.call.heard(speech())
        rows = journal.recent(self.conn, limit=5)
        texts = [r["text"] for r in rows]
        self.assertIn("how are you doing", texts)
        self.assertTrue(any(t.startswith("Very good, sir.") for t in texts))

    def test_each_turn_logs_its_latency_stage_by_stage(self):
        self.listen()
        status, body = self.request("POST", "/api/call/hear?vad=550&during=0", speech(), ctype="audio/wav")
        turn = json.loads(body)["turn"]
        self.next("say")
        self.request("POST", "/api/call/latency", json.dumps({"turn": turn, "vad_ms": 550, "heard_to_voice_ms": 1200}).encode())
        rows = {}
        for line in call.latency_log().read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            rows.setdefault(r["key"], {}).update(r)
        row = list(rows.values())[0]
        for stage in ("stt_s", "brain_s", "tts_s", "server_s", "vad_s", "heard_to_voice_s"):
            self.assertIn(stage, row)
        self.assertEqual(row["brain"], "quick")
        self.assertEqual(row["heard_to_voice_s"], 1.2)
        report = call.latency_report()
        self.assertIn("whisper", report)
        self.assertIn("1 of 1 turns", report)


class QuickBrainUnitTest(BrainTest):
    def test_sentences_stream_and_the_handoff_marker_is_never_spoken(self):
        brain = quick.QuickBrain(FAKE_QUICK)
        try:
            said = []
            job = brain.reply("what did we sell today", said.append)
            self.assertEqual(said, ["Let me check that, sir."])
            self.assertEqual(job, "Tell the owner what sold today. Then give the ad spend.")
            said = []
            self.assertEqual(brain.reply("hello there", said.append), "")
            self.assertEqual(said[0], "Very good, sir.")
        finally:
            brain.stop()

    def test_what_the_main_session_said_reaches_the_next_quick_turn(self):
        brain = quick.QuickBrain([*FAKE_QUICK, "--fake-echo-context"])
        try:
            said = []
            brain.reply("(JARVIS said: Four brushes, sir.)\nhello", said.append)
            self.assertIn("Four brushes", " ".join(said))
        finally:
            brain.stop()

    def test_off_means_no_quick_model(self):
        self.init()
        paths.set_config("call_quick_model", "off")
        self.assertIsNone(quick.command())
        self.assertFalse(quick.QuickBrain().available())


class FakeBrain:
    """A quick brain that notes what it was sent and answers in one line."""

    def __init__(self, line="Right, sir. Which one?"):
        self.sent, self.line, self.proc, self.started = [], line, None, 0.0

    def available(self):
        return True

    def warm(self):
        pass

    def reply(self, text, on_sentence, on_first=None, recap=""):
        self.sent.append((text, recap))
        on_sentence(self.line)
        return ""

    def stop(self):
        pass


class HumanCallTest(BrainTest):
    """Calls should feel like talking to a person (T100 follow-up 4)."""

    def setUp(self):
        super().setUp()
        self.init()
        self.brain = FakeBrain()
        self.call = call.Call(call.Ears(FAKE_STT), call.Jarvis("http://127.0.0.1:9"), self.brain)
        self.call._journal = lambda *a: None

    def queued(self):
        out = []
        while not self.call.lines.empty():
            out.append(self.call.lines.get()[1])
        return out

    def test_ids_paths_links_markdown_and_as_an_ai_are_never_spoken(self):
        said = call.speakable("**Done** (T84, M-261109-cad3): saved to brain/work/general/T100-notes.md, see "
                              "https://example.com/x. As an AI, I think W19 is fine 🙂")
        self.assertEqual(said, "Done: saved to the file, see the link. I think is fine")
        self.assertEqual(call.speakable("- First, the brush"), "First, the brush")
        self.assertEqual(call.speakable("A$380/week target"), "A$380/week target")
        self.call.say("T84")
        self.call.say("Sorted, sir (A12).")
        self.assertEqual(self.queued(), ["Sorted, sir."])

    def test_the_hello_is_warm_and_knows_the_time_of_day_and_what_is_waiting(self):
        self.assertTrue(call.opening(9).startswith("Morning, sir."))
        self.assertTrue(call.opening(14).startswith("Afternoon, sir."))
        self.assertTrue(call.opening(20).startswith("Evening, sir."))
        self.assertTrue(call.opening(2).startswith("You're up late, sir."))
        self.conn.execute("INSERT INTO approvals (ts, kind, summary, status) VALUES ('2026-11-09', 'spend', 'test', 'pending')")
        self.conn.commit()
        self.assertEqual(call.opening(9), "Morning, sir. There is one approval waiting on you. What can I do for you?")

    def test_the_first_turn_tells_the_quick_brain_how_the_call_opened(self):
        self.call.greeting = lambda: "Afternoon, sir. What can I do for you?"
        self.call.hello()
        self.call._quick("check the brush", 1)
        self.assertIn("You opened it with: \"Afternoon, sir. What can I do for you?\"", self.brain.sent[0][0])
        self.call._quick("and the spend", 2)
        self.assertNotIn("You opened it with", self.brain.sent[1][0])
        recap = self.brain.sent[1][1]
        self.assertIn("Owner: check the brush", recap)
        self.assertIn("JARVIS: Right, sir. Which one?", recap)

    def test_after_being_talked_over_it_knows_what_the_owner_actually_heard(self):
        self.call._quick("how did the ads go", 1)
        self.call.voiced = {1: ["Well, the brush"]}
        self.call.last_source = "quick"
        self.call.hush()
        self.call._quick("hang on, the face one", 2)
        self.assertIn("they only heard: \"Well, the brush\"", self.brain.sent[1][0])
        self.call._quick("go on", 3)
        self.assertNotIn("only heard", self.brain.sent[2][0])

    def test_a_long_main_session_reply_is_cut_to_the_gist(self):
        c = self.call
        c.listeners.append(queue.Queue())  # a phone on the call
        events = [{"type": "say_chunk", "text": "Sentence number %d is here and rather long, sir, as these go." % i}
                  for i in range(12)] + [{"type": "status", "status": "idle"},
                                         {"type": "say_chunk", "text": "A new reply."}]
        c.jarvis.events = lambda: iter(events)
        real_sleep = time.sleep

        def stop(_):
            raise SystemExit
        call.time.sleep = stop
        try:
            with self.assertRaises(SystemExit):
                c._relay_loop()
        finally:
            call.time.sleep = real_sleep
        said = self.queued()
        self.assertLessEqual(len(" ".join(said[:-2])), call.MAIN_MAX_CHARS + 70)
        self.assertEqual(said[-2:], [call.MORE, "A new reply."])

    def test_a_fresh_quick_brain_gets_the_call_so_far(self):
        brain = quick.QuickBrain([*FAKE_QUICK, "--fake-echo-recap"])
        try:
            said = []
            brain.reply("hello", said.append, recap="Owner: how is the brush going")
            self.assertEqual(" ".join(said), "Recap: Owner: how is the brush going")
            said = []
            brain.reply("hello", said.append, recap="Owner: how is the brush going")
            self.assertTrue(said[0].startswith("Very good"))  # the live process already has it
        finally:
            brain.stop()

    def test_the_phone_says_mm_sparingly(self):
        from jarvis import sip

        class Rtp:
            talking, out = False, queue.Queue()
            played = []

            def play(self, ulaw, tag=None):
                self.played.append(ulaw)

        c = self.call
        c.heard = lambda *a: None
        rtp = Rtp()
        leg = sip.PhoneLeg(c, rtp)
        leg.clips = {"ack": [b"mm"]}
        for heard in (2000, 2000, 2000, 600):
            leg.heard_ms, leg.frames = heard, [sip.ulaw_to_pcm(b"\xff" * 160)]
            leg._finish()
        self.assertEqual(len(rtp.played), 2)  # turns one and three; never twice running, never for a short "yes"


class EchoTest(CallTest):
    def test_jarvis_hearing_his_own_last_line_is_dropped(self):
        self.listen()
        c = self.server.call
        c.said.append((time.time(), "Four brushes sold today, sir, and the spend is twenty dollars."))
        c.ears.hear = lambda wav: "brushes sold today sir and the spend"
        c.heard(speech())
        self.assertEqual(self.next("echo")["text"], "brushes sold today sir and the spend")
        self.assertEqual(self.dash.asked, [])

    def test_the_owner_repeating_a_word_is_not_echo_unless_jarvis_was_talking(self):
        said = ["Shall I pause the brush ads?"]
        self.assertFalse(call.echo_of("pause", said, during=False))
        self.assertTrue(call.echo_of("pause", said, during=True))
        self.assertFalse(call.echo_of("no leave them running and check the spend", said, during=True))

    def test_his_own_mm_coming_back_is_dropped_when_he_was_talking(self):
        self.listen()
        c = self.server.call
        c.ears.hear = lambda wav: "Right."
        c.heard(speech(), during=True)
        self.assertEqual(self.next("echo")["text"], "Right.")
        c.heard(speech(), during=False)
        self.assertEqual(self.next("heard")["text"], "Right.")

    def test_old_lines_no_longer_count(self):
        c = self.server.call
        c.said.append((time.time() - call.ECHO_WINDOW - 1, "what did we sell today"))
        c.heard(speech())
        self.assertEqual(self.dash.asked, [call.PREFIX + "what did we sell today"])

    def test_whisper_phantoms_are_dropped(self):
        c = self.server.call
        c.ears.hear = lambda wav: "Thanks for watching!"
        c.heard(speech())
        self.assertEqual(self.dash.asked, [])

    def test_the_page_gates_the_mic_and_asks_for_echo_cancellation(self):
        html = call.PAGE.read_text(encoding="utf-8")
        for needed in ("echoCancellation", "noiseSuppression", "autoGainControl", "BARGE_FRAMES", "during=",
                       "/api/call/latency"):
            self.assertIn(needed, html)
