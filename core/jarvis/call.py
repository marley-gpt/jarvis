"""JARVIS calls: a live, hands-free voice call between the owner's phone and JARVIS, coded here, no call service.

The phone opens a private call page (a link with a long secret key, sent by Telegram). The page listens
on the phone's mic, notices when the owner stops talking and sends that bit of speech here. This server
turns it into text with local Whisper (stt_worker.py), hands it to the same JARVIS conversation the
dashboard and Telegram use (the dashboard's /api/chat), and speaks every sentence of the reply back in
JARVIS's voice (the dashboard's /api/tts) as soon as it's written. Talking over JARVIS stops him.

It is a separate small server on purpose: the only thing reachable from outside the PC is this call page,
and only with the key. The dashboard itself stays loopback-only. To reach it from anywhere, a free
Cloudflare quick tunnel (cloudflared, no account) gives it an https address, which the phone's browser
needs before it will share the mic.

    bin/jarvis call serve      run the call server (the app starts it in the background)
    bin/jarvis call link       print the current call link, starting the server and tunnel if needed
    bin/jarvis call ring       JARVIS calls the owner: a Telegram message with an Answer button
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import paths, telegram, tts

PORT = 8770
DASHBOARD = "http://127.0.0.1:8765"
PAGE = paths.CORE_DIR / "dashboard" / "call.html"
PREFIX = ("<call>Live phone call with the owner on the JARVIS call page. Talk like a real person on the phone, not a "
          "chatbot: react to what he actually said, use contractions and plain everyday words, one to three short "
          "sentences, and ask a quick question back when a person naturally would. You've already said a quick "
          "\"mm\" or \"right\" out loud, so don't open with filler or \"Certainly\"; go straight to it. No lists, "
          "links or markdown. If the work is long, start it in the background and say so.</call> ")  # a tag, so the journal keeps only the owner's words
HELLO = "JARVIS here, sir. Go ahead."
# Little things a person says on a call, voiced once and kept on the phone, so they play instantly:
# a reaction the moment the owner stops talking, an "mm-hm" while he's in full flow, a filler if JARVIS needs a moment.
CLIPS = {
    "ack": ["Mm.", "Right.", "Okay.", "Got it.", "Sure.", "Yep."],
    "hmm": ["Mm-hm.", "Mm.", "Yeah."],
    "filler": ["Let me have a look, sir.", "One moment, sir.", "Bear with me a second.", "Right, let me check."],
}
MAX_AUDIO = 4 * 1024 * 1024  # about two minutes of 16 kHz speech
NO_WINDOW, DETACHED = 0x08000000, 0x00000008
TRAILING = re.compile(r"(\b(and|so|but|or|um|uh|the|a|to|because|like)|,)\s*$", re.I)  # he hasn't finished
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def call_dir() -> Path:
    d = paths.home() / "call"
    d.mkdir(parents=True, exist_ok=True)
    return d


def call_key() -> str:
    """The secret in the call link. Kept across restarts; anyone without it gets nothing."""
    path = paths.home() / "secrets" / "call_key"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if not key:
        key = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
    return key


def dashboard_token() -> str:
    try:
        return (paths.home() / "secrets" / "dashboard_token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def cloudflared() -> str | None:
    found = paths.home() / "tools" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    if found.exists():
        return str(found)
    from shutil import which
    installed = Path(os.environ.get("LOCALAPPDATA", "")) / "cloudflared" / "cloudflared.exe"  # winget's place (A31)
    return which("cloudflared") or (str(installed) if os.name == "nt" and installed.exists() else None)


# ---------------------------------------------------------------- the parts

class Ears:
    """Local speech-to-text: one long-lived stt_worker.py in the brain/tts venv, loaded on first use."""

    def __init__(self, command: list[str] | None = None):
        self.command = command
        self.proc = None
        self.lock = threading.Lock()

    def _command(self) -> list[str] | None:
        if self.command:
            return self.command
        venv = tts.tts_dir() / "venv"
        py = venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"
        if not py.exists():
            return None
        return [str(py), str(paths.CORE_DIR / "jarvis" / "stt_worker.py"), paths.config().get("stt_model", "small.en")]

    def _start(self) -> None:
        cmd = self._command()
        if not cmd:
            raise RuntimeError("Speech recognition isn't installed (brain/tts/venv with faster-whisper).")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1,
                                     creationflags=NO_WINDOW if os.name == "nt" else 0)
        first = json.loads(self.proc.stdout.readline() or "{}")
        if not first.get("ready"):
            raise RuntimeError("Speech recognition didn't start.")

    def warm(self) -> None:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()

    def hear(self, wav: bytes) -> str:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()
            self.proc.stdin.write(json.dumps({"wav": base64.b64encode(wav).decode()}) + "\n")
            self.proc.stdin.flush()
            reply = json.loads(self.proc.stdout.readline() or "{}")
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error") or "Speech recognition stopped.")
        return reply["text"]

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


class Jarvis:
    """The dashboard, spoken to over loopback, so a call is the same conversation as the dashboard and Telegram."""

    def __init__(self, base: str = DASHBOARD):
        self.base = base

    def _post(self, path: str, body: dict, timeout: float = 30):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Jarvis-Token": dashboard_token()})
        return urllib.request.urlopen(req, timeout=timeout)

    def ask(self, text: str, patience: float = 120) -> None:
        """Send what the owner said. If JARVIS is mid-reply (a phone message, say), wait for his turn."""
        deadline = time.time() + patience
        while True:
            try:
                self._post("/api/chat", {"text": PREFIX + text}).close()
                return
            except urllib.error.HTTPError as exc:
                if exc.code != 409 or time.time() > deadline:
                    raise RuntimeError(json.loads(exc.read() or b"{}").get("error") or f"JARVIS said {exc.code}")
            time.sleep(1)

    def voice(self, text: str) -> bytes:
        with self._post("/api/tts", {"text": text}, timeout=120) as r:
            return r.read()

    def events(self):
        """JARVIS's live events (what he's saying, when he's done), for as long as the dashboard runs."""
        with urllib.request.urlopen(self.base + "/api/events", timeout=60) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data: "):
                    yield json.loads(line[6:])


class Call:
    """Everything one call page needs: what was heard, what JARVIS says (as audio, in order), his state."""

    WAIT_FOR_MORE = 1.6  # seconds to wait when the owner trails off mid-thought

    def __init__(self, ears: Ears, jarvis: Jarvis):
        self.ears, self.jarvis = ears, jarvis
        self.listeners: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.lines: queue.Queue = queue.Queue()  # sentences waiting to be voiced
        self.hush_at = 0.0
        self.talked_over = False  # the rest of the current reply is dropped until JARVIS finishes it
        self.carry, self.carry_id = "", 0  # words the owner trailed off on ("...and"), waiting for the rest
        self.clips: dict[str, list[dict]] = {}

    def send_clips(self, after: float = 0.0) -> None:
        """Voice the reactions (once per server run; `after` lets the greeting go first) and hand them to the phone."""
        if not self.clips:
            time.sleep(after)
            try:
                self.clips = {kind: [{"text": t, "wav": base64.b64encode(self.jarvis.voice(t)).decode()} for t in lines]
                              for kind, lines in CLIPS.items()}
            except (OSError, urllib.error.URLError) as exc:
                sys.stderr.write(f"jarvis call: reactions not voiced: {exc}\n")
                return
        self.publish({"type": "clips", "clips": self.clips})

    @staticmethod
    def greeting() -> str:
        """What JARVIS opens with when he rang the owner (`call ring --say`), said once."""
        path = call_dir() / "greeting.txt"
        try:
            text = path.read_text(encoding="utf-8").strip()
            path.unlink()
        except OSError:
            return ""
        return text

    # the page's event stream
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def live(self) -> bool:
        return bool(self.listeners)

    def publish(self, ev: dict) -> None:
        with self.lock:
            for q in self.listeners:
                q.put(ev)

    # talking
    def say(self, text: str) -> None:
        text = " ".join((text or "").split())
        if text:
            self.lines.put((time.time(), text))

    def hush(self) -> None:
        """The owner talked over JARVIS: drop what he hadn't said yet, and the rest of that reply."""
        self.hush_at = time.time()
        self.talked_over = True

    def _next_line(self) -> tuple[float, str]:
        """The next thing to say, joined with any short pieces right behind it ("Hello," "sir,"), so it flows."""
        queued, text = self.lines.get()
        while queued < self.hush_at:  # talked over: never said
            queued, text = self.lines.get()
        while len(text) < 60:
            try:
                later, more = self.lines.get(timeout=0.3)
            except queue.Empty:
                break
            if later >= self.hush_at:
                text += " " + more
        return queued, text

    def _voice_loop(self) -> None:
        while True:
            queued, text = self._next_line()
            if queued < self.hush_at or not self.live():
                continue
            try:
                wav = self.jarvis.voice(text)
            except (OSError, urllib.error.URLError) as exc:
                self.publish({"type": "say", "text": text, "error": str(exc)})
                continue
            if queued >= self.hush_at:
                self.publish({"type": "say", "text": text, "wav": base64.b64encode(wav).decode()})

    def _relay_loop(self) -> None:
        """Turn JARVIS's live events into speech on the call."""
        while True:
            try:
                for ev in self.jarvis.events():
                    if not self.live():
                        continue
                    kind = ev.get("type")
                    if kind == "status" and ev.get("status") == "idle":
                        self.talked_over = False
                    if self.talked_over and kind in ("say_chunk", "say"):
                        continue
                    if kind == "say_chunk":
                        self.say(ev.get("text", ""))
                    elif kind == "say" and not ev.get("streamed"):
                        self.say(ev.get("text", ""))
                    elif kind == "status":
                        self.publish({"type": "status", "status": ev.get("status")})
                    elif kind == "error":
                        self.say(ev.get("text", ""))
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(2)  # the dashboard is restarting; reconnect

    def start(self) -> None:
        threading.Thread(target=self._voice_loop, name="call-voice", daemon=True).start()
        threading.Thread(target=self._relay_loop, name="call-relay", daemon=True).start()
        threading.Thread(target=self._warm, name="call-warm", daemon=True).start()

    def _warm(self) -> None:
        try:
            self.ears.warm()
        except (RuntimeError, OSError, ValueError) as exc:
            sys.stderr.write(f"jarvis call: speech recognition not ready: {exc}\n")
        if not self.live():
            self.send_clips()  # voiced ahead of the first call, so they're there from its first second

    def heard(self, wav: bytes) -> None:
        """A stretch of the owner's speech: to text, then to JARVIS."""
        self.publish({"type": "status", "status": "thinking"})
        try:
            text = self.ears.hear(wav).strip()
        except (RuntimeError, OSError, ValueError) as exc:
            return self.say(f"Sorry sir, my hearing has dropped out. {exc}")
        if not re.search(r"[a-zA-Z0-9]", text):
            return self.publish({"type": "status", "status": "idle"})
        if self.carry:  # the rest of a thought he trailed off on: this turn takes it over
            text, self.carry_id = f"{self.carry} {text}", self.carry_id + 1
        self.publish({"type": "heard", "text": text})
        if TRAILING.search(text):  # "...and" / "so," : give him a moment to finish the thought
            self.carry_id += 1
            self.carry, mine = text, self.carry_id
            time.sleep(self.WAIT_FOR_MORE)
            if self.carry_id != mine:
                return  # more speech came and took this with it
        self.carry = ""
        self.talked_over = False
        try:
            self.jarvis.ask(text)
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            self.say(f"I can't reach my own brain just now, sir. {exc}")


# ---------------------------------------------------------------- HTTP

def make_server(host: str = "127.0.0.1", port: int = PORT, ears: Ears | None = None,
                jarvis: Jarvis | None = None) -> ThreadingHTTPServer:
    call = Call(ears or Ears(), jarvis or Jarvis())
    key = call_key()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # the key is in the address; never pass it on
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _key_ok(self, given: str) -> bool:
            return bool(given) and hmac.compare_digest(given.encode(), key.encode())

        def do_GET(self):
            route, _, query = self.path.partition("?")
            q = parse_qs(query)
            if route == "/api/call/ping":
                return self._json(200, {"ok": True})
            if not self._key_ok((q.get("k") or [""])[0]):
                return self._json(403, {"error": "This call link isn't valid."})
            if route == "/call":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if route == "/api/call/events":
                return self._events()
            self._json(404, {"error": "not found"})

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q = call.subscribe()
            try:
                self._event({"type": "connected"})
                while True:
                    try:
                        self._event(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                call.unsubscribe(q)

        def _event(self, data: dict) -> None:
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_AUDIO:
                self.close_connection = True
                return self._json(413, {"error": "too long"})
            raw = self.rfile.read(length)
            if not self._key_ok(self.headers.get("X-Call-Key", "")):
                return self._json(403, {"error": "This call link isn't valid."})
            if self.path == "/api/call/hear":
                if raw[:4] != b"RIFF":
                    return self._json(400, {"error": "expected a WAV"})
                threading.Thread(target=call.heard, args=(raw,), daemon=True).start()
                return self._json(202, {"ok": True})
            if self.path == "/api/call/hello":
                call.hush_at = 0.0
                call.say(call.greeting() or HELLO)
                threading.Thread(target=call.send_clips, args=(2.5,), daemon=True).start()
                return self._json(200, {"ok": True})
            if self.path == "/api/call/hush":
                call.hush()
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = os.name != "nt"

        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], ConnectionError):
                super().handle_error(request, client_address)

    server = Server((host, port), Handler)
    server.call, server.key = call, key
    return server


# ---------------------------------------------------------------- the tunnel and the link

class Tunnel:
    """A free Cloudflare quick tunnel to the call server: an https address that works from anywhere."""

    def __init__(self, port: int = PORT):
        self.port, self.proc, self.url = port, None, ""

    def start(self) -> None:
        exe = cloudflared()
        if not exe:
            return
        self.proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", errors="replace",
                                     creationflags=NO_WINDOW if os.name == "nt" else 0)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stderr:
            found = TUNNEL_URL.search(line)
            if found and found.group(0) != self.url:
                self.url = found.group(0)
                (call_dir() / "tunnel.json").write_text(json.dumps({"url": self.url, "at": time.time()}),
                                                        encoding="utf-8")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def serve(port: int = PORT) -> None:
    try:
        server = make_server(port=port)
    except OSError:
        print(f"The call server is already running on port {port}.", flush=True)
        return
    (call_dir() / "tunnel.json").unlink(missing_ok=True)
    tunnel = Tunnel(port)
    if not paths.config().get("call_base_url"):  # a private address (Tailscale) needs no public tunnel
        tunnel.start()
    server.call.start()
    print(f"JARVIS calls on http://127.0.0.1:{port}/call" +
          ("" if cloudflared() else "  (no cloudflared, so only on this PC)"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.stop()
        server.call.ears.stop()


def running(port: int = PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/call/ping", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def start_background() -> None:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    log = paths.scratch_dir() / "call.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        subprocess.Popen([exe, str(paths.CORE_DIR / "jarvis_cli.py"), "call", "serve"], cwd=paths.REPO_ROOT,
                         stdout=out, stderr=out, stdin=subprocess.DEVNULL, close_fds=True,
                         creationflags=NO_WINDOW | DETACHED if os.name == "nt" else 0)


def link(wait: float = 40) -> str:
    """The call link, starting the server and tunnel if needed. Local-only if there's no tunnel.
    `call_base_url` in brain/config.json (a private address such as Tailscale's https name) wins over the tunnel."""
    if not running():
        start_background()
    base = paths.config().get("call_base_url")
    if base:
        return f"{base.rstrip('/')}/call?k={call_key()}"
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            url = json.loads((call_dir() / "tunnel.json").read_text(encoding="utf-8"))["url"]
            return f"{url}/call?k={call_key()}"
        except (OSError, ValueError, KeyError):
            pass
        if running() and not cloudflared():
            break
        time.sleep(0.5)
    return f"http://127.0.0.1:{PORT}/call?k={call_key()}"


def ring(say: str = "", voice_ring: bool = False) -> str:
    """JARVIS calls the owner: a Telegram message with an Answer button (and the phone ring if asked)."""
    url = link()
    if say:
        (call_dir() / "greeting.txt").write_text(say, encoding="utf-8")
    if url.startswith("http://127.0.0.1"):
        raise RuntimeError("The call can only be answered on this PC: cloudflared isn't installed, so there's no "
                           "address the phone can reach.")
    telegram.send("📞 JARVIS is calling, sir.", markup={"inline_keyboard": [[{"text": "📞 Answer", "url": url}]]})
    if voice_ring:
        telegram.ring()
    return url
