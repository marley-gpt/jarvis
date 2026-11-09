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

Calls v2 (T100): the back-and-forth is answered by a quick model (quick.py) that hands real work to the main
session, every turn's latency is logged per stage (`call latency`), and JARVIS's own voice coming back through
the phone's speaker is dropped (the page gates the mic while he talks; transcripts that match what he just said
are thrown away). A real ringing phone, with no second number, comes from sip.py: a free SIP app on the phone
registered to this server.

Calls v3 (T100, D66): talks like a person. A warm hello with the time of day and the one thing that matters
(opening), short turns that react and ask back (quick.PROMPT), nothing unsayable reaches the voice (speakable: no
ids, paths, links, markdown, "as an AI"), the main session is cut to the gist on a call, the "mm" plays now and then
rather than every turn, and the quick brain keeps the thread (the call so far if it restarts, what the owner
actually heard when they talked over it).

    bin/jarvis call serve      run the call server (the app starts it in the background)
    bin/jarvis call link       print the current call link, starting the server and tunnel if needed
    bin/jarvis call ring       JARVIS calls the owner: the SIP phone rings if one is registered, else Telegram
    bin/jarvis call latency    how fast the last calls answered, stage by stage
    bin/jarvis call sip        set up the SIP softphone (prints the owner's steps and the account to enter)
"""

from __future__ import annotations

import base64
import collections
import difflib
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

from . import paths, quick, telegram, tts

PORT = 8770
DASHBOARD = "http://127.0.0.1:8765"
PAGE = paths.CORE_DIR / "dashboard" / "call.html"
PREFIX = ("<call>Live phone call with the owner. Talk like a real person on the phone, not a chatbot: react to "
          "what they actually said, contractions and plain everyday words, ONE or TWO short sentences, then a quick "
          "follow-up question when a person naturally would. A short \"mm\" may already have played, so don't open "
          "with filler or \"Certainly\". Never read out ids, task numbers, file paths or links, no lists or markdown, "
          "never \"as an AI\". Give the headline, not the detail; offer the rest. If the work is long, start it in "
          "the background and say so.</call> ")  # a tag, so the journal keeps only the owner's words
HELLO = "JARVIS here, sir. Go ahead."  # when the greeting can't be worked out
MAIN_MAX_CHARS = 420  # about four short sentences of the main session's reply; the rest stays in the chat
QUICK_MAX_CHARS = 200  # the quick brain stops being voiced once a turn has run this long
MORE = "That's the gist, sir. The rest is in the chat if you want it."
NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
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
# What Whisper "hears" in a breath or a bit of noise, never what the owner said.
PHANTOM = re.compile(r"^\W*(thanks? (you )?for watching|please subscribe|subtitles by.*|\.+)\W*$", re.I)
ECHO_WINDOW = 20.0  # seconds a line JARVIS said can still come back through the phone's speaker


SPOKEN_JUNK = [
    (re.compile(r"(?:https?://|www\.)\S+?(?=[.,;:!?)]*(?:\s|$))"), "the link"),
    (re.compile(r"(?:[A-Za-z]:[\\/]|\.{0,2}/)?(?:[\w.-]+[\\/])+[\w-]+\.\w{1,5}\b"), "the file"),  # brain/work/x.md
    (re.compile(r"\b(?:brain|core|bin|scripts|\.claude)[\\/][\w./\\-]*"), "the file"),
    (re.compile(r"\bM-\d{6}-[0-9a-f]{4}\b"), ""),  # memory ids
    (re.compile(r"\b[TWDAJLH]\d{1,4}\b"), ""),  # task, worker, decision, approval... ids
    (re.compile(r"\bas an (?:ai|artificial intelligence)(?: (?:language )?model)?,?\s*", re.I), ""),
    (re.compile(r"[*_`#>|]+"), ""),  # markdown
    (re.compile(r"^\s*(?:[-•]|\d+[.)])\s+"), ""),  # a list bullet
    (re.compile("[\U0001F000-\U0001FFFF☀-➿️]"), ""),  # emoji
]


def speakable(text: str) -> str:
    """What can be said out loud: no links, paths, ids, markdown, bullets, emoji or "as an AI"."""
    for pattern, repl in SPOKEN_JUNK:
        text = pattern.sub(repl, text)
    text = re.sub(r"\(\s*[,;]?\s*\)|\[\s*\]", "", text)  # brackets left empty
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
    return " ".join(text.split()).lstrip(",;: ")


def count(n: int, thing: str) -> str:
    word = NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)
    return f"{word} {thing}{'' if n == 1 else 's'}"


def opening(hour: int | None = None) -> str:
    """A warm hello with the owner's context: the time of day and the one thing that matters right now."""
    hour = time.localtime().tm_hour if hour is None else hour
    if hour < 5:
        hi = "You're up late, sir."
    elif hour < 12:
        hi = "Morning, sir."
    elif hour < 18:
        hi = "Afternoon, sir."
    else:
        hi = "Evening, sir."
    news = ""
    try:
        from . import db, sales_watch, work
        conn = db.connect()
        try:
            sold = sales_watch.figures(conn, paths.now()[:10])["sales_count"]
            waiting = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0]
        finally:
            conn.close()
        running = sum(1 for r in work.listing(10) if r.get("status") == "running")
        if sold:
            news = f"Good news first, {count(sold, 'sale')} in today."
        elif waiting:
            news = f"There {'is' if waiting == 1 else 'are'} {count(waiting, 'approval')} waiting on you."
        elif running:
            news = f"I've {count(running, 'job')} running in the background."
    except Exception:  # the hello must never fail; it just has less to say
        pass
    return " ".join(p for p in (hi, news, "What can I do for you?") if p)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def echo_of(heard: str, said: list[str], during: bool = False) -> bool:
    """Is this transcript JARVIS's own voice, picked up from the phone's speaker? Most of its words appear, in
    order, in something he just said. A word or two only counts if the mic opened while he was talking."""
    got = words(heard)
    if not got:
        return False
    for line in said:
        his = words(line)
        if not his:
            continue
        match = sum(b.size for b in difflib.SequenceMatcher(None, got, his, autojunk=False).get_matching_blocks())
        if len(got) <= 2:
            if during and match == len(got):
                return True
        elif match / len(got) >= 0.6:
            return True
    return False


def latency_log() -> Path:
    return call_dir() / "latency.jsonl"


def log_latency(row: dict) -> None:
    try:
        with open(latency_log(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def latency_report(last: int = 10) -> str:
    """The last turns, stage by stage: the page's end-of-turn wait, Whisper, the brain's first sentence, the
    voice's first audio, and (from the phone) the owner's last word to JARVIS's first sound."""
    rows: dict[int, dict] = {}
    try:
        for line in latency_log().read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            rows.setdefault(r.get("key") or r.get("turn"), {}).update(r)
    except OSError:
        return "No call latency logged yet."
    turns = [r for r in rows.values() if "stt_s" in r][-last:]
    if not turns:
        return "No call latency logged yet."
    cols = [("vad_s", "end-of-turn"), ("upload_s", "upload"), ("stt_s", "whisper"), ("brain_s", "brain 1st"),
            ("tts_s", "voice 1st"), ("server_s", "server total"), ("heard_to_voice_s", "phone: last word→sound")]
    out = ["| when | via | brain | " + " | ".join(c[1] for c in cols) + " | said |",
           "|---" * (len(cols) + 4) + "|"]
    for r in turns:
        cells = [f"{r[k]:.2f}" if isinstance(r.get(k), (int, float)) else "" for k, _ in cols]
        out.append(f"| {r.get('at', '')[11:19]} | {r.get('via', 'page')} | {r.get('brain', '')} | "
                   + " | ".join(cells) + f" | {r.get('text', '')[:40]} |")
    goal = [r["heard_to_voice_s"] for r in turns if isinstance(r.get("heard_to_voice_s"), (int, float))]
    if goal:
        out.append(f"\nFirst sound under 1.5 s after the owner stopped: {sum(g <= 1.5 for g in goal)} of {len(goal)} turns "
                   f"(median {sorted(goal)[len(goal) // 2]:.2f} s).")
    return "\n".join(out)


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
        return [str(py), str(paths.CORE_DIR / "jarvis" / "stt_worker.py"), paths.config().get("stt_model", "base.en")]

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
    """Everything a call needs, whichever way the owner is on it (the call page, or a SIP phone through sip.py):
    what was heard, what JARVIS says (as audio, in order), his state."""

    WAIT_FOR_MORE = 1.6  # seconds to wait when the owner trails off mid-thought

    def __init__(self, ears: Ears, jarvis: Jarvis, brain: quick.QuickBrain | None = None):
        self.ears, self.jarvis = ears, jarvis
        self.brain = brain if brain is not None else quick.QuickBrain()
        self.listeners: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.lines: queue.Queue = queue.Queue()  # (queued at, text, turn, source) waiting to be voiced
        self.held = None  # a line taken off the queue that belongs to the next reply
        self.voiced_turn = 0  # the last turn whose opening words went to the voice
        self.hush_at = 0.0
        self.hushes = 0  # bumps each time the owner talks over JARVIS: a quick reply in flight stops speaking
        self.talked_over = False  # the rest of the main session's current reply is dropped until it finishes
        self.last_source, self.last_said_at = "", 0.0
        self.carry, self.carry_id = "", 0  # words the owner trailed off on ("...and"), waiting for the rest
        self.clips: dict[str, list[dict]] = {}
        self.said: collections.deque = collections.deque(maxlen=30)  # (when, text) JARVIS said: for echo checks
        self.main_said: list[str] = []  # what the main session said on the call, for the quick brain's next turn
        self.history: list[str] = []  # the call so far ("Owner: ..." / "JARVIS: ..."), if the quick brain restarts
        self.opened = ""  # the greeting, told to the quick brain on the call's first turn
        self.main_chars = 0  # how much of the main session's current reply has been said
        self.cut = False  # the owner talked over the quick brain's last reply
        self.last_quick = 0  # that reply's turn
        self.voiced: dict[int, list[str]] = {}  # turn -> the quick lines actually sent to the phone
        self.turn = 0
        self.turns: dict[int, dict] = {}  # per-turn latency stamps
        self.session = ""

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

    def hello(self) -> None:
        """A call has connected: greet the owner and get the quick brain ready while the greeting plays."""
        self.hush_at = 0.0
        self.session = "call-" + time.strftime("%Y%m%d-%H%M%S")
        self.main_said = []
        try:
            hello = self.greeting() or opening()
        except Exception:
            hello = HELLO
        self.opened, self.history, self.cut = hello, [f"JARVIS: {hello}"], False
        self.say(hello)
        threading.Thread(target=self.send_clips, args=(2.5,), daemon=True).start()
        threading.Thread(target=self._warm_brain, daemon=True).start()

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
    def say(self, text: str, turn: int = 0, source: str = "main") -> None:
        text = speakable(text or "")
        if re.search(r"[A-Za-z0-9]", text):
            self.lines.put((time.time(), text, turn, source))

    def hush(self) -> None:
        """The owner talked over JARVIS: drop what he hadn't said yet. If it was the main session talking, the rest
        of that reply goes too; a quick reply just stops (the main session may still be working on a handoff)."""
        self.hush_at = time.time()
        self.hushes += 1
        if self.last_source != "quick":
            self.talked_over = True
        else:
            self.cut = True

    def _next_line(self) -> tuple[float, str, int, str]:
        """The next thing to say, joined with any short pieces right behind it ("Hello," "sir,"), so it flows.
        Only pieces of the same reply are joined: a quick answer and the main session's are voiced apart."""
        queued, text, turn, source = self.held or self.lines.get()
        self.held = None
        while queued < self.hush_at:  # talked over: never said
            queued, text, turn, source = self.lines.get()
        if turn and turn != self.voiced_turn:  # a reply's opening words go at once, alone: they're what feels quick
            self.voiced_turn = turn
            return queued, text, turn, source
        while len(text) < 60:
            try:
                nxt = self.lines.get(timeout=0.12)  # short: the first words matter most
            except queue.Empty:
                break
            if nxt[2:] != (turn, source):
                self.held = nxt
                break
            if nxt[0] >= self.hush_at:
                text += " " + nxt[1]
        return queued, text, turn, source

    def _voice_loop(self) -> None:
        while True:
            queued, text, turn, source = self._next_line()
            if queued < self.hush_at or not self.live():
                continue
            try:
                wav = self.jarvis.voice(text)
            except (OSError, urllib.error.URLError) as exc:
                self.publish({"type": "say", "text": text, "error": str(exc)})
                continue
            if queued >= self.hush_at:
                self.said.append((time.time(), text))
                self.last_source, self.last_said_at = source, time.time()
                if source == "quick":
                    self.voiced.setdefault(turn, []).append(text)
                    for old in [k for k in self.voiced if k < turn - 5]:
                        del self.voiced[old]
                self._stamp(turn, "voice")
                self.publish({"type": "say", "text": text, "wav": base64.b64encode(wav).decode(), "turn": turn})

    def _relay_loop(self) -> None:
        """Turn the main session's live events into speech on the call."""
        while True:
            try:
                for ev in self.jarvis.events():
                    if not self.live():
                        continue
                    kind = ev.get("type")
                    if kind == "status" and ev.get("status") == "idle":
                        self.talked_over = False
                        self.main_chars = 0
                    if self.talked_over and kind in ("say_chunk", "say"):
                        continue
                    if kind == "say_chunk" or (kind == "say" and not ev.get("streamed")) or kind == "error":
                        text = ev.get("text", "").strip()
                        turn = self.turn if self.brain_off() else 0
                        if kind != "error" and self.main_chars >= MAIN_MAX_CHARS:  # no monologues on a call
                            if self.main_chars < MAIN_MAX_CHARS * 10:
                                self.main_chars = MAIN_MAX_CHARS * 10
                                self.say(MORE, turn)
                            continue
                        self.main_chars += len(text)
                        if text:
                            self.main_said.append(text)
                            self.history.append(f"JARVIS: {speakable(text)}")
                        self.say(text, turn)
                    elif kind == "status":
                        self.publish({"type": "status", "status": ev.get("status")})
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
        self._warm_brain()
        if not self.live():
            self.send_clips()  # voiced ahead of the first call, so they're there from its first second

    def _warm_brain(self) -> None:
        if self.brain_off():
            return
        try:
            if self.brain.proc and time.time() - self.brain.started > 1800:
                self.brain.new_call()  # a fresh brief for a call made long after the last
            self.brain.warm()
        except (RuntimeError, OSError) as exc:
            sys.stderr.write(f"jarvis call: quick model not ready: {exc}\n")

    def brain_off(self) -> bool:
        return not self.brain.available()

    # latency
    def new_turn(self, via: str = "page", **page) -> int:
        with self.lock:
            self.turn += 1
            n = self.turn
            self.turns[n] = {"turn": n, "key": f"{int(time.time())}-{n}", "via": via, "t0": time.time(),
                             "at": paths.now(), **page}
            for old in [k for k in self.turns if k < n - 20]:
                del self.turns[old]
        return n

    def _stamp(self, turn: int, stage: str) -> None:
        row = self.turns.get(turn)
        if not row or stage in row:
            return
        row[stage] = time.time()
        if stage == "voice":
            t0 = row["t0"]
            done = {k: v for k, v in row.items() if k not in ("t0", "heard", "first", "voice")}
            done.update(stt_s=round(row.get("heard", t0) - t0, 3),
                        brain_s=round(row.get("first", row["voice"]) - row.get("heard", t0), 3),
                        tts_s=round(row["voice"] - row.get("first", row["voice"]), 3),
                        server_s=round(row["voice"] - t0, 3))
            log_latency(done)

    def page_latency(self, turn: int, data: dict) -> None:
        """The phone's side of a turn: how long its end-of-turn wait was, and last word to first sound."""
        row = self.turns.get(turn)
        if not row:
            return
        keep = {k: round(float(data[k]) / 1000, 3) for k in ("vad_ms", "upload_ms", "heard_to_voice_ms")
                if isinstance(data.get(k), (int, float))}
        log_latency({"key": row["key"], **{k.replace("_ms", "_s"): v for k, v in keep.items()}})

    # listening
    def heard(self, wav: bytes, turn: int = 0, during: bool = False) -> None:
        """A stretch of the owner's speech: to text, then to JARVIS. `during`: the mic opened while JARVIS
        was speaking, so it may be his own voice coming back."""
        turn = turn or self.new_turn()
        self.publish({"type": "status", "status": "thinking"})
        try:
            text = self.ears.hear(wav).strip()
        except (RuntimeError, OSError, ValueError) as exc:
            return self.say(f"Sorry sir, my hearing has dropped out. {exc}", turn)
        self._stamp(turn, "heard")
        if not re.search(r"[a-zA-Z0-9]", text) or PHANTOM.match(text):
            return self.publish({"type": "status", "status": "idle"})
        recent = [t for when, t in list(self.said) if time.time() - when < ECHO_WINDOW]
        if during:  # his little "mm" and "right" come back through the speaker too
            recent += [t for kind in CLIPS.values() for t in kind]
        if echo_of(text, recent, during):
            self.publish({"type": "echo", "text": text})  # his own voice from the phone's speaker: not the owner
            return self.publish({"type": "status", "status": "idle"})
        if self.carry:  # the rest of a thought he trailed off on: this turn takes it over
            text, self.carry_id = f"{self.carry} {text}", self.carry_id + 1
        self.publish({"type": "heard", "text": text, "turn": turn})
        if TRAILING.search(text):  # "...and" / "so," : give him a moment to finish the thought
            self.carry_id += 1
            self.carry, mine = text, self.carry_id
            time.sleep(self.WAIT_FOR_MORE)
            if self.carry_id != mine:
                return  # more speech came and took this with it
        self.carry = ""
        if self.turns.get(turn) is not None:
            self.turns[turn]["text"] = text
        if not self.brain_off():
            try:
                return self._quick(text, turn)
            except (RuntimeError, OSError) as exc:
                sys.stderr.write(f"jarvis call: quick model failed, asking the main session: {exc}\n")
        if self.turns.get(turn) is not None:
            self.turns[turn]["brain"] = "main"
        if self.history[-1:] != [f"Owner: {text}"]:  # not already there from a quick turn that failed
            self.history.append(f"Owner: {text}")
        self.talked_over, self.main_chars = False, 0
        try:
            self.jarvis.ask(text)
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            self.say(f"I can't reach my own brain just now, sir. {exc}", turn)

    def _quick(self, text: str, turn: int) -> None:
        """The quick brain answers at once; anything it can't do goes to the main session in the background."""
        if self.turns.get(turn) is not None:
            self.turns[turn]["brain"] = "quick"
        context = "".join(f"(JARVIS said: {line})\n" for line in self.main_said[-6:])
        self.main_said = []
        if self.cut:  # talked over last time: tell it what the owner actually heard
            heard = " ".join(self.voiced.get(self.last_quick, []))
            context = f"(The owner talked over your last reply; they only heard: \"{heard or 'nothing'}\")\n" + context
            self.cut = False
        if self.opened:  # the call's first turn (the brain may still remember an earlier call)
            context = (f"(A new call has just connected at {time.strftime('%H:%M')}. You opened it with: "
                       f"\"{self.opened}\")\n") + context
        recap = "\n".join(self.history[-16:])
        self.history.append(f"Owner: {text}")
        mark, spoken = self.hushes, []

        def sentence(s: str) -> None:
            if self.hushes == mark and len(" ".join(spoken)) < QUICK_MAX_CHARS:  # a backstop on rambling
                spoken.append(s)
                self.say(s, turn, "quick")

        try:
            handoff = self.brain.reply(context + text, sentence, on_first=lambda: self._stamp(turn, "first"),
                                       recap=recap)
        except RuntimeError:
            if not spoken:
                raise  # nothing said yet: the main session takes the turn
            handoff = ""  # it stopped part way: what was said stands, never asked twice
        self.opened, self.last_quick = "", turn
        if spoken:
            self.history.append("JARVIS: " + speakable(" ".join(spoken)))
        self._journal(text, " ".join(spoken))
        if handoff:
            self.talked_over, self.main_chars = False, 0
            threading.Thread(target=self._hand_off, args=(handoff, text), daemon=True).start()
        else:
            self.publish({"type": "status", "status": "idle"})

    def _hand_off(self, job: str, said: str) -> None:
        try:
            self.jarvis.ask(f"{job}\n(The owner's words on the call: \"{said}\")")
        except (RuntimeError, OSError, urllib.error.URLError) as exc:
            self.say(f"I couldn't pass that to the rest of me, sir. {exc}")

    def _journal(self, owner: str, reply: str) -> None:
        """Quick-brain turns skip the main session's hooks, so they're journaled here."""
        try:
            from . import db, journal
            conn = db.connect()
            try:
                journal.record(conn, "owner", owner, self.session or "call")
                if reply:
                    journal.record(conn, "jarvis", reply, self.session or "call")
            finally:
                conn.close()
        except Exception as exc:  # the call goes on even if the journal can't be written
            sys.stderr.write(f"jarvis call: journal: {exc}\n")


# ---------------------------------------------------------------- HTTP

def make_server(host: str = "127.0.0.1", port: int = PORT, ears: Ears | None = None,
                jarvis: Jarvis | None = None, brain: quick.QuickBrain | None = None) -> ThreadingHTTPServer:
    call = Call(ears or Ears(), jarvis or Jarvis(), brain)
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
            route, _, query = self.path.partition("?")
            if route == "/api/call/hear":
                if raw[:4] != b"RIFF":
                    return self._json(400, {"error": "expected a WAV"})
                q = parse_qs(query)
                during = (q.get("during") or ["0"])[0] == "1"  # the mic opened while JARVIS was talking
                try:
                    vad = {"vad_ms": float((q.get("vad") or [""])[0])}
                except ValueError:
                    vad = {}
                turn = call.new_turn("page", **{k.replace("_ms", "_s"): round(v / 1000, 3) for k, v in vad.items()})
                threading.Thread(target=call.heard, args=(raw, turn, during), daemon=True).start()
                return self._json(202, {"ok": True, "turn": turn})
            if route == "/api/call/hello":
                call.hello()
                return self._json(200, {"ok": True})
            if route == "/api/call/hush":
                call.hush()
                return self._json(200, {"ok": True})
            if route == "/api/call/sip":  # the CLI on this PC asks the call server to ring the SIP phone
                server_sip = getattr(self.server, "sip", None)
                body = json.loads(raw or b"{}") if raw else {}
                if not server_sip:
                    return self._json(200, {"enabled": False, "result": "no phone"})
                if body.get("ring"):
                    if body.get("say"):
                        (call_dir() / "greeting.txt").write_text(body["say"], encoding="utf-8")
                    return self._json(200, {"enabled": True, "result": server_sip.ring(
                        ring_for=float(body.get("ring_for") or 45))})
                return self._json(200, {"enabled": True, **server_sip.status()})
            if route == "/api/call/quit":  # the routines tick restarts a server running on old settings
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self._json(200, {"ok": True})
            if route == "/api/call/latency":
                try:
                    data = json.loads(raw or b"{}")
                    call.page_latency(int(data.get("turn") or 0), data)
                except (ValueError, TypeError):
                    return self._json(400, {"error": "bad latency report"})
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = os.name != "nt"

        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], ConnectionError):
                super().handle_error(request, client_address)

    server = Server((host, port), Handler)
    server.call, server.key, server.sip = call, key, None
    return server


def start_sip(server) -> None:
    """The SIP phone line (sip.py), if it's set up ("sip_bind" in brain/config.json)."""
    from . import sip
    s = sip.settings()
    if not s["bind"]:
        return
    try:
        server.sip = sip.SipServer(server.call, s["bind"], s["port"], s["rtp_port"], provider=s["provider"],
                                   provider_password=sip.provider_password(), trusted=s["trusted"],
                                   provider_ha1=sip.provider_ha1())
        server.sip.start()
        print(f"JARVIS phone line (SIP) on {server.sip.ip}:{server.sip.port}", flush=True)
    except OSError as exc:
        sip.log(f"couldn't listen on {s['bind']}:{s['port']}: {exc} (another SIP app on this PC holding the port?)")
        print(f"The SIP phone line couldn't start on {s['bind']}:{s['port']}: {exc}", flush=True)


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
    if paths.standby():
        print("This machine is the JARVIS standby, so the call server stays off "
              "(run `bin/jarvis machine primary` to take over).", flush=True)
        return
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
    start_sip(server)
    print(f"JARVIS calls on http://127.0.0.1:{port}/call" +
          ("" if cloudflared() else "  (no cloudflared, so only on this PC)"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.stop()
        server.call.ears.stop()
        server.call.brain.stop()
        if server.sip:
            server.sip.stop()


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


def sip_request(body: dict, timeout: float = 5) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/call/sip", json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Call-Key": call_key()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except (OSError, ValueError):
        return {"enabled": False, "result": "no call server"}


def ring_phone(say: str = "") -> str:
    """Ring the SIP phone (the native incoming-call screen). "answered", "declined", "no answer", "no phone"..."""
    if not paths.config().get("sip_bind"):
        return "no phone"
    if not running():
        start_background()
        deadline = time.time() + 20
        while not running() and time.time() < deadline:
            time.sleep(0.5)
        time.sleep(1)
    return sip_request({"ring": True, "say": say}, timeout=60).get("result", "no phone")


def keep_line(settle: float = 120) -> str:
    """The routines tick keeps the call server, and with it JARVIS's phone line, up 24/7 on the current settings:
    starts it if it's down, restarts it (never mid-call) if it runs on old settings or its line has lapsed."""
    cfg = paths.config()
    if not cfg.get("sip_bind"):
        return "phone line off"
    if not running():
        start_background()
        return "call server was down: started"
    st = sip_request({})
    if st.get("on_call"):
        return "on a call"
    want = bool((cfg.get("sip_provider") or {}).get("line"))
    line = (st.get("provider") or {}).get("line")
    stale = not st.get("enabled") or want != bool(line)
    lapsed = bool(line) and not line["on"] and line.get("up_for", 0) > settle
    if not (stale or lapsed):
        return ("line signed in" if line["on"] else "line signing in") if line else "phone line up"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/call/quit", b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Call-Key": call_key()})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except OSError:
        pass
    deadline = time.time() + 15
    while running() and time.time() < deadline:
        time.sleep(0.5)
    start_background()
    why = "it ran on old settings" if stale else f"its line had lapsed ({(st.get('provider') or {}).get('error') or 'no answer'})"
    return f"call server restarted: {why}"


def ring(say: str = "", voice_ring: bool = False) -> str:
    """JARVIS calls the owner. The SIP phone rings like a real call if one is set up; if it isn't, or the
    owner doesn't pick up, a Telegram message with an Answer button (and CallMeBot's ring if asked)."""
    result = ring_phone(say)
    if result == "answered":
        return "sip: answered"
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
