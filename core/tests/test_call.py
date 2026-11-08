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

from jarvis import call

FAKE_STT = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_stt.py")]


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

    def setUp(self):
        super().setUp()
        self.init()
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
        self.assertEqual(hello["text"], call.HELLO)
        self.assertEqual(base64.b64decode(hello["wav"]), b"RIFF" + call.HELLO.encode())
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
        c.lines.put((time.time() - 5, "An old sentence the owner talked over."))
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
