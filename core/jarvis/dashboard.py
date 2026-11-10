"""The JARVIS app: a product-testing cockpit, and the way to talk to JARVIS.

`bin/jarvis dashboard` serves one app on 127.0.0.1 (core/dashboard/, see its README): a few pages inside one
shell (Testing, Team, Work, Money, Brain, System) with a talk panel on every page. All of them draw from one
state (cockpit.py) that arrives on the live event stream, so nothing polls. Talking to JARVIS runs one headless
Claude Code process (`claude -p`, stream-json in and out) that stays running between messages and resumes the saved
conversation if restarted, so it uses the owner's existing Claude subscription. Pasted images go in with the
message. Each delegation (Agent tool call) and each specialist's tool use is pushed to the page as it happens.
Durable state (tasks, approvals, memory, journal) comes from the brain, so the app also shows work done from any
other Claude Code session.

Listening uses the local ears (or the browser's speech recognition); speaking uses local Kokoro
(core/jarvis/tts.py) with the browser's voice as a fallback, so it costs nothing extra.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from . import cockpit, control, db, journal, live, lookup, memory, paths, records, roster, skills, swarm, telegram, tts, visuals, wakeword

STATIC = paths.CORE_DIR / "dashboard"
# One app (T110, D75): every page route serves the same shell, and the page inside it changes without a reload.
# A deeper path opens a record on that page: /work/T110, /team/creative-director, /brain/M-261110-cdd9.
APP_PAGES = ("testing", "team", "work", "money", "brain", "system")
SHELL = "app.html"
# Addresses of the pages that were removed (the 3D HQ, the separate chat, the office, console, stage and brain
# graph): an old bookmark or shortcut lands on the app instead of an error.
RETIRED = ("/app", "/hq", "/chat", "/office", "/console", "/stage", "/classic")
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".ico": "image/x-icon"}
ASSET_NAME = re.compile(r"^/[a-z0-9][a-z0-9-]*\.(?:js|css|ico)$")
AGENTS_DIR = paths.REPO_ROOT / ".claude" / "agents"

# Display order and short names; mentor first because every other agent depends on it.
AGENT_LABELS = {
    "mentor-learning": "Mentor Notes",
    "product-research": "Product Research",
    "market-research": "Customer & Market",
    "creative-research": "Creative Research",
    "creative-director": "Creative Director",
    "shopify": "Shopify",
    "store-qa": "Store QA",
    "meta-ads": "Meta Ads",
    "meta-ads-auditor": "Meta Auditor",
    "product-testing": "Product Testing",
    "operations": "Operations",
    "accountant": "Accountant",
}

# Kept free of cmd.exe metacharacters: on Windows `claude` is often a .cmd shim.
VOICE_PROMPT = (
    "The owner is talking to you through the JARVIS dashboard, often by voice, and your replies are read aloud "
    "in a British voice. Speak as JARVIS from the films: a calm, courteous British butler and engineer, "
    "unflappable, precise and quietly capable, with a dry and understated wit used lightly. "
    "Use British spelling and phrasing. You may address the owner as sir now and then, but sparingly, "
    "never in every reply. Talk the way he does in the films, as the jarvis-voice skill sets out: "
    "give status crisply with the number first; offer the next step as a question that starts with Shall I; "
    "take an instruction with a short As you wish or Very good; deliver bad news calmly and plainly, "
    "prefaced with I am afraid or Unfortunately, then say what it means and what you suggest; "
    "push back politely when something is unwise, and if the owner insists, comply with at most one dry aside; "
    "look after the owner, mentioning rest if they have been at it very late. "
    "Talk like a person, not a system: never mention message numbers, queues or that you will be with them shortly, "
    "and never apologise for being a machine. If several messages arrive together, just answer them naturally in one reply. "
    "Be brief: a few short spoken sentences, no tables, no code blocks, no headings, "
    "no lists, no markdown. Lead with the answer, then the one thing that matters next. "
    "When you hand work to specialists, say plainly who is doing what. Put detail in deliverable files, "
    "not in the spoken reply. Say record ids such as T12, A3, D7 or X1 and real numbers plainly: the page turns each id into a link to the record. Never invent figures or results. Consequential actions such as spending, "
    "launching or changing ads, prices or the live store still need the owner's approval first."
)


# What a freshly started Claude Code is sent to warm it up (answered locally, no usage), and how long a message
# waits for that before giving up on the process.
PRIME_COMMAND = "/cost"
PRIME_WAIT = 60

# Claude Code's own wording when it has no credentials, and when a resumed conversation is gone.
LOGIN_PROBLEM = re.compile(r"^\s*(not logged in|invalid api key|please run /login)", re.I)
MISSING_SESSION = re.compile(r"no conversation found", re.I)
LOGIN_HELP = ("I can't answer yet because Claude Code isn't signed in on this computer. "
              "Open a terminal, run claude once and sign in with your Claude account, then talk to me again.")


# ---------------------------------------------------------------- state

def agent_roster() -> list[dict]:
    found = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        meta, _ = memory.parse(path.read_text(encoding="utf-8"))
        name = meta.get("name") or path.stem
        desc = meta.get("description", "")
        role = re.split(r"(?<=\.)\s", desc, maxsplit=1)
        found[name] = {
            "name": name,
            "label": AGENT_LABELS.get(name, name.replace("-", " ").title()),
            "role": role[1] if len(role) > 1 else desc,
            "title": role[0].rstrip("."),
            "model": meta.get("model", "inherit"),
        }
    order = [n for n in AGENT_LABELS if n in found] + [n for n in found if n not in AGENT_LABELS]
    return [found[n] for n in order]


# ---------------------------------------------------------------- live events

class Hub:
    """Fan-out of live events to every open page."""

    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, event: dict) -> None:
        with self.lock:
            for q in self.subs:
                q.put(event)


def find_claude() -> list[str] | None:
    cfg = paths.config().get("claude_command")
    if cfg:
        return cfg if isinstance(cfg, list) else [cfg]
    exe = shutil.which("claude")
    if not exe:
        # The native installer puts it here but doesn't always add the folder to PATH.
        native = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        exe = str(native) if native.exists() else None
    return [exe] if exe else None


def _summary(tool: str, data: dict) -> str:
    for key in ("description", "command", "file_path", "pattern", "query", "url", "prompt"):
        if isinstance(data.get(key), str):
            return journal.short(f"{tool}: {data[key]}", 110)
    return tool


IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
MAX_IMAGES, MAX_IMAGE_BYTES = 4, 5 * 1024 * 1024
# base64 images and attached files, plus the text
MAX_BODY = (MAX_IMAGES * MAX_IMAGE_BYTES + control.MAX_FILES * control.MAX_FILE_BYTES) * 4 // 3 + 64 * 1024
# Where streamed text is cut into sentences to speak: end punctuation followed by a space, or a line break.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_END = re.compile(r"(?<=[,;:])\s+")   # the opening clause goes to the voice early, so the first word comes sooner


def save_images(images: list) -> tuple[list[dict], list[str]]:
    """Validate pasted images; returns Claude content blocks and the brain paths they were saved to."""
    if len(images) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} images per message.")
    blocks, saved = [], []
    folder = paths.files_dir() / "uploads"
    stamp = paths.now()[:19].replace("-", "").replace(":", "").replace(" ", "-").replace("T", "-")
    for n, img in enumerate(images, 1):
        kind = (img or {}).get("media_type")
        if kind not in IMAGE_TYPES:
            raise ValueError("Images must be PNG, JPEG, GIF or WebP.")
        try:
            data = base64.b64decode(img.get("data") or "", validate=True)
        except (ValueError, TypeError):
            raise ValueError("That image couldn't be read.")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Each image must be under 5 MB.")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stamp}-{n}.{IMAGE_TYPES[kind]}"
        path.write_bytes(data)
        saved.append(paths.rel(path))
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": kind, "data": img["data"]}})
    return blocks, saved


class Runner:
    """One conversation with JARVIS through headless Claude Code.

    The Claude Code process stays running between messages (stream-json in and out), so a reply
    doesn't wait for Claude Code to start up each time, and its text is streamed sentence by
    sentence ("say_chunk") so the voice can start speaking before the reply is finished. If the
    process ends (stopped, crashed, signed out) the next message starts a new one that resumes
    the saved conversation.
    """

    def __init__(self, hub: Hub):
        self.hub = hub
        self.proc: subprocess.Popen | None = None
        self.stderr: list[str] = []
        self.lock = threading.Lock()
        self.live: dict = {"status": "idle", "agents": {}, "activity": {}}
        self.buf = ""          # streamed text not yet spoken
        self.streamed = False  # this text block was already spoken in chunks
        self.last_output: float | None = None  # when Claude Code last said anything, while a reply is running
        self.hung = False      # the watchdog ended a frozen reply
        self.failed = ""       # Claude Code ended the turn with an error and no answer
        # The control centre (T98): permission prompts answered on the page, the phone or by voice; messages that
        # arrive while JARVIS is busy, answered in order; the steps he has run; the commands and skills he has.
        self.permissions = control.Permissions(hub)
        self.queue: list[dict] = []
        self.steps: list[dict] = []
        self.commands: dict = {"slash": [], "skills": []}
        self.slash = False     # this turn is a slash command: its output is shown, not spoken
        self.closed = False    # the server is shutting down: nothing is started again
        self.prime = False     # serve() turns it on: a started Claude Code is primed before the first message
        self.primed = threading.Event()   # clear while a priming exchange has the process's output
        self.primed.set()

    @property
    def busy(self) -> bool:
        return self.live["status"] != "idle"

    def session(self) -> str | None:
        conn = db.connect()
        try:
            return db.get_meta(conn, "dashboard_session")
        finally:
            conn.close()

    def reset(self, announce: bool = True) -> None:
        self.close()
        conn = db.connect()
        try:
            db.set_meta(conn, "dashboard_session", "")
        finally:
            conn.close()
        if announce:
            self.hub.publish({"type": "reset"})

    def warm(self) -> None:
        """Start Claude Code now (resuming the saved conversation) so the first message doesn't pay for its start-up
        and the session hooks. Best effort, in the background."""
        def go():
            proc = None
            try:
                with self.lock:
                    claude = find_claude()
                    if claude and not (self.proc and self.proc.poll() is None) and not self.busy and not self.closed:
                        self._spawn(claude, self.session())
                        if self.prime:
                            proc = self.proc
                            self.primed.clear()
                if proc:
                    self._prime(proc)
            except OSError:
                pass
            finally:
                if proc:
                    self.primed.set()
        threading.Thread(target=go, daemon=True).start()

    def _prime(self, proc: subprocess.Popen) -> None:
        """Make a freshly started Claude Code do its slow first-message work now: it only loads its connectors and
        settings when the first message arrives, which cost the owner's first message about ten seconds. `/cost` is
        answered by Claude Code itself, so this spends no usage and adds nothing to the conversation. The next
        message waits for it to finish (see _turn)."""
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": PRIME_COMMAND}]}}
        self._send(proc, json.dumps(msg) + "\n")
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init" and isinstance(ev.get("slash_commands"), list):
                self.commands = {"slash": sorted(str(c) for c in ev["slash_commands"]),
                                 "skills": sorted(str(c) for c in ev.get("skills") or [])}
            if ev.get("type") == "result":
                return

    def start(self, text: str, images: list | None = None, files: list | None = None, queue: bool = False) -> str:
        """Send a message. Busy: with `queue` it waits its turn ("queued"), otherwise RuntimeError."""
        claude = find_claude()
        if not claude:
            raise FileNotFoundError(
                "Claude Code CLI not found. Install it (it uses your existing Claude subscription), "
                "or set \"claude_command\" in brain/config.json.")
        with self.lock:
            if self.busy and not queue:
                raise RuntimeError("JARVIS is still answering the last message.")
        blocks, saved = save_images(images or [])
        if saved:
            text = (text or "Take a look at this.") + (
                "\n\n(Image" + ("s" if len(saved) > 1 else "") + " attached above, also saved at "
                + ", ".join(saved) + ": pass the path to any agent that needs to see it.)")
        attached = control.save_files(files or [])
        if attached:
            text = (text or "Take a look at this.") + (
                "\n\n(File" + ("s" if len(attached) > 1 else "") + " attached, saved at " + ", ".join(attached)
                + ": read it from there and pass the path to any agent that needs it.)")
        with self.lock:
            if self.busy:
                if not queue:
                    raise RuntimeError("JARVIS is still answering the last message.")
                self.queue.append({"text": text, "blocks": blocks, "at": time.time()})
                self.hub.publish({"type": "queued", "text": text, "waiting": len(self.queue)})
                return "queued"
            self._launch(claude, text, blocks)
        return "started"

    def _launch(self, claude: list[str], text: str, blocks: list[dict]) -> None:
        self.live = {"status": "thinking", "request": text, "agents": {}, "activity": {}}
        self.slash = text.lstrip().startswith("/")
        self._step("owner", text, "done")
        self.hub.publish({"type": "status", "status": "thinking", "request": text})
        threading.Thread(target=self._turn, args=(claude, text, blocks), daemon=True).start()

    def _next(self) -> None:
        """The turn ended: answer the next message that was waiting, if any."""
        with self.lock:
            if self.busy or not self.queue:
                return
            claude = find_claude()
            if not claude:
                return
            msg = self.queue.pop(0)
            self._launch(claude, msg["text"], msg["blocks"])

    def unqueue(self, index: int) -> None:
        with self.lock:
            if not 0 <= index < len(self.queue):
                raise ValueError("No such waiting message.")
            self.queue.pop(index)

    def switch(self, sid: str | None) -> None:
        """Carry on another Claude Code conversation (or a new one): the next message resumes it."""
        self.close()
        conn = db.connect()
        try:
            db.set_meta(conn, "dashboard_session", sid or "")
        finally:
            conn.close()
        self.steps = []
        self.hub.publish({"type": "session", "id": sid})
        self.warm()

    def _step(self, kind: str, text: str, state: str = "running", sid: str | None = None) -> dict:
        step = {"id": sid, "kind": kind, "text": journal.short(text, 300), "state": state, "ts": paths.now()}
        self.steps.append(step)
        del self.steps[:-200]
        return step

    def stop(self) -> None:
        """Interrupt the reply. The process ends and a new one starts at once on the same conversation, so the
        next message doesn't wait for Claude Code to start."""
        self.close()
        self.warm()

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()  # first: closing stdin waits for a message that's still being written
            try:
                proc.stdin.close()
            except OSError:
                pass

    def _spawn(self, claude: list[str], sid: str | None) -> None:
        cfg = paths.config()
        cmd = [*claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--permission-mode", "acceptEdits",
               # Prompts the Claude Code window would show come to the page instead (control.Permissions).
               "--permission-prompt-tool", "stdio", "--append-system-prompt", VOICE_PROMPT]
        if cfg.get("dashboard_model"):
            cmd += ["--model", cfg["dashboard_model"]]
        if cfg.get("dashboard_effort"):
            cmd += ["--effort", cfg["dashboard_effort"]]
        if sid:
            cmd += ["--resume", sid]
        self.stderr = []
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", cwd=paths.REPO_ROOT,
                                     creationflags=paths.NO_WINDOW)
        threading.Thread(target=self._drain, args=(self.proc, self.stderr), daemon=True).start()

    @staticmethod
    def _drain(proc: subprocess.Popen, sink: list[str]) -> None:
        for line in proc.stderr:  # keep stderr flowing so the process never blocks on it
            sink.append(line)
            del sink[:-50]

    def _turn(self, claude: list[str], text: str, images: list[dict]) -> None:
        try:
            if not self.primed.wait(PRIME_WAIT):   # the warm-up never finished: that process is no use
                self.close()
                self.primed.set()
            for attempt in (1, 2):
                sid = self.session()
                if not (self.proc and self.proc.poll() is None):
                    self._spawn(claude, sid)
                self.hung, self.failed = False, ""
                answered, err = self._exchange(text, images)
                if answered:
                    break
                if attempt == 1 and self.hung:
                    continue  # it froze: resume the conversation and send the message again
                if attempt == 1 and sid and self.failed:
                    self.reset(announce=False)  # memory lives in the brain, so a new conversation loses nothing
                    continue
                if self.hung:
                    err = "I got stuck on that one, sir. Would you mind sending it again?"
                if attempt == 1 and sid and MISSING_SESSION.search(err):
                    # The saved conversation is gone (e.g. cleared by Claude Code): start a fresh one.
                    self.reset(announce=False)
                    continue
                if self.live.get("stopped"):
                    break
                self.hub.publish({"type": "error", "text": journal.short(err or "JARVIS stopped without answering.", 400)})
                break
        except OSError as exc:
            self.hub.publish({"type": "error", "text": f"Could not start Claude Code: {exc}"})
        finally:
            self.live = {"status": "idle", "agents": {}, "activity": self.live.get("activity", {})}
            for step in self.steps:
                if step["state"] == "running":
                    step["state"] = "done"
            self.hub.publish({"type": "status", "status": "idle"})
            self._next()

    def _watchdog(self, proc: subprocess.Popen) -> None:
        """Claude Code can freeze without an error (it once sat silent for 7 hours, with every phone message
        queued behind it). No output for too long means it's stuck: end it, and the turn retries."""
        limit = paths.config().get("reply_timeout_seconds", 900)
        while self.last_output and self.proc is proc and proc.poll() is None:
            if time.time() - self.last_output > limit:
                self.hung = True
                proc.kill()
                return
            time.sleep(min(5, limit / 10))

    @staticmethod
    def _send(proc: subprocess.Popen, line: str) -> None:
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except (OSError, ValueError):  # the process went away mid-message
            pass

    def _exchange(self, text: str, images: list[dict]) -> tuple[bool, str]:
        """Send one message to the running process and read events until its result."""
        proc = self.proc
        dispatched: dict[str, str] = {}
        self.buf, self.streamed = "", False
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}, *images]}}
        # A photo doesn't fit in the pipe. Writing it before reading left both sides waiting on each other
        # if Claude Code was still waiting for its own output to be read, so send while reading, watchdog on.
        self.last_output = time.time()
        threading.Thread(target=self._watchdog, args=(proc,), daemon=True).start()
        threading.Thread(target=self._send, args=(proc, json.dumps(msg) + "\n"), daemon=True).start()
        try:
            for line in proc.stdout:
                self.last_output = time.time()
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if self._handle(ev, dispatched):
                    self.last_output = None
                    return (False, self.failed) if self.failed else (True, "")
        except (OSError, ValueError):  # the process went away mid-message
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if self.proc is proc:
            self.proc = None
        else:
            self.live["stopped"] = True  # stop() closed it on purpose
        time.sleep(0.05)  # let the stderr reader catch the last lines
        return False, "".join(self.stderr).strip()

    def _stream(self, ev: dict) -> None:
        """Speak top-level text as it arrives, a sentence at a time."""
        e = ev.get("event") or {}
        if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
            piece = e["delta"].get("text", "")
            self.buf += piece
            if piece and not self.slash:   # the words as they are written: the page shows them before the voice has them
                self.hub.publish({"type": "say_delta", "text": piece})
            if self.buf.rfind("[[") > self.buf.rfind("]]"):
                return  # a screen hint is still arriving: never split it
            *done, self.buf = SENTENCE_END.split(self.buf)
            for sentence in done:  # a short opener ("Very good, sir.") is spoken at once: it's what feels quick
                self._chunk(sentence)
            if not self.streamed and len(self.buf) > 24:   # nothing spoken yet and a long first sentence: say its first clause now
                *lead, self.buf = CLAUSE_END.split(self.buf)
                for clause in lead:
                    self._chunk(clause)
        elif e.get("type") == "content_block_stop" and self.buf.strip():
            self._chunk(self.buf)
            self.buf = ""

    def _chunk(self, text: str) -> None:
        if visuals.strip_markers(text) == "" and visuals.MARKER.search(text):  # a hint on its own: shown, not spoken
            self.streamed = True
            self.hub.publish({"type": "say_chunk", "text": "", "raw": text.strip()})
            return
        if text.strip() and not LOGIN_PROBLEM.search(text):
            self.streamed = True
            self.hub.publish({"type": "say_chunk", "text": visuals.strip_markers(text), "raw": text.strip()})

    def _login_needed(self, needed: bool) -> None:
        conn = db.connect()
        try:
            if bool(db.get_meta(conn, "claude_login_needed")) != needed:
                db.set_meta(conn, "claude_login_needed", "1" if needed else "")
        finally:
            conn.close()

    def _permit(self, proc: subprocess.Popen, ev: dict) -> None:
        """Answer one control request from Claude Code. A permission prompt waits for the owner; the watchdog
        is held off meanwhile, since silence here is the owner thinking, not Claude Code frozen."""
        rid, req = ev.get("request_id"), ev.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            reply = {"subtype": "error", "request_id": rid, "error": f"unsupported: {req.get('subtype')}"}
        else:
            def alive():
                if self.last_output:
                    self.last_output = time.time()
                return self.proc is proc and proc.poll() is None
            reply = {"subtype": "success", "request_id": rid, "response": self.permissions.ask(rid, req, alive)}
        self._send(proc, json.dumps({"type": "control_response", "response": reply}) + "\n")

    def _handle(self, ev: dict, dispatched: dict) -> bool:
        kind = ev.get("type")
        if kind == "control_request":
            threading.Thread(target=self._permit, args=(self.proc, ev), daemon=True).start()
            return False
        if kind == "system" and ev.get("subtype") == "init" and isinstance(ev.get("slash_commands"), list):
            self.commands = {"slash": sorted(str(c) for c in ev["slash_commands"]),
                             "skills": sorted(str(c) for c in ev.get("skills") or [])}
        if kind == "stream_event":
            if not ev.get("parent_tool_use_id"):
                self._stream(ev)
            return False
        if kind == "result":
            text = ev.get("result") or ""
            if ev.get("is_error") and LOGIN_PROBLEM.search(text):
                self._login_needed(True)
                self.hub.publish({"type": "reply", "text": LOGIN_HELP, "is_error": True, "login": True})
                return True
            self._login_needed(False)
            if ev.get("is_error") and not text:
                # Failed before answering, with nothing to show the owner (a lost conversation does this):
                # the turn starts afresh instead of going silent. Don't save this run's session id.
                self.failed = "; ".join(str(e) for e in ev.get("errors") or []) or ev.get("subtype") or "no reason given"
                sys.stderr.write(f"jarvis: reply failed: {self.failed}\n")
                return True
            if not text:  # the owner would otherwise wait on an answer that isn't coming
                sys.stderr.write(f"jarvis: reply ended with nothing to say: {ev.get('subtype')}\n")
                self.hub.publish({"type": "error", "text": "I lost my thread on that one, sir. Would you mind sending it again?"})
        if ev.get("session_id") and kind in ("system", "result"):
            conn = db.connect()
            try:
                db.set_meta(conn, "dashboard_session", ev["session_id"])
            finally:
                conn.close()
        parent = ev.get("parent_tool_use_id")
        message = ev.get("message")  # some system events carry plain text here, which once killed the reply
        content = message.get("content") if isinstance(message, dict) else None
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    if not parent and LOGIN_PROBLEM.search(block["text"]):
                        continue  # reported once, in plain words, with the result
                    if parent and parent in dispatched:
                        self._agent_line(dispatched[parent], journal.short(block["text"], 110))
                    elif not parent:
                        self.hub.publish({"type": "say", "text": visuals.strip_markers(block["text"]), "raw": block["text"],
                                          "streamed": self.streamed, "slash": self.slash})
                        self.streamed = False
                elif block.get("type") == "tool_use":
                    name, data = block.get("name", ""), block.get("input") or {}
                    if name in ("Agent", "Task"):
                        agent = data.get("subagent_type") or "general-purpose"
                        dispatched[block.get("id")] = agent
                        self.live["agents"][agent] = {"description": data.get("description")}
                        self.live["status"] = "delegating"
                        self._step("agent", f"{agent}: {data.get('description') or ''}", sid=block.get("id"))
                        swarm.note(agent, f"Took on: {data.get('description') or 'a job'}", "start")
                        self.hub.publish({"type": "dispatch", "agent": agent, "description": data.get("description"),
                                          "id": block.get("id")})
                    elif parent and parent in dispatched:
                        self._agent_line(dispatched[parent], _summary(name, data))
                    elif not parent:
                        self._step("tool", control.describe(name, data), sid=block.get("id"))
                        self.hub.publish({"type": "jarvis_tool", "summary": _summary(name, data), "id": block.get("id"), "tool": name})
        elif kind == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if not parent:
                        self._step_done(block)
                    agent = dispatched.pop(block.get("tool_use_id"), None)
                    if agent:
                        self.live["agents"].pop(agent, None)
                        if not self.live["agents"]:
                            self.live["status"] = "thinking"
                        swarm.note(agent, "Finished and handed back", "done")
                        self.hub.publish({"type": "agent_done", "agent": agent, "id": block.get("tool_use_id")})
        elif kind == "result":
            self.hub.publish({"type": "reply", "text": ev.get("result") or "", "is_error": bool(ev.get("is_error")),
                              "cost_usd": ev.get("total_cost_usd"), "slash": self.slash})
            return True
        return False

    def _step_done(self, block: dict) -> None:
        """A top-level tool (or a hand-off) finished: mark its step with the outcome and a short look at the output."""
        tid = block.get("tool_use_id")
        step = next((x for x in reversed(self.steps) if x["id"] and x["id"] == tid), None)
        if not step:
            return
        out = block.get("content")
        if isinstance(out, list):
            out = " ".join(b.get("text", "") for b in out if isinstance(b, dict))
        step["state"] = "error" if block.get("is_error") else "done"
        step["out"] = journal.short(str(out or "").strip(), 400)
        self.hub.publish({"type": "jarvis_tool_done", "id": tid, "ok": step["state"] == "done", "out": step["out"]})

    def _agent_line(self, agent: str, line: str) -> None:
        self.live["activity"].setdefault(agent, []).append(line)
        self.live["activity"][agent] = self.live["activity"][agent][-6:]
        swarm.note(agent, line)
        self.hub.publish({"type": "agent_activity", "agent": agent, "line": line})


# ---------------------------------------------------------------- debate mode

DEBATE_KINDS = {"product": "a product pick", "kill": "a kill-or-continue call on a test", "scale": "a scale call",
                "other": "a big call"}


def start_debate(conn, body: dict) -> dict:
    """Start a durable worker that argues a big call out with the marketing-council skill and records the outcome.
    It recommends; anything that spends, launches or changes the store still goes through the owner's approval."""
    from . import work
    question = " ".join(str(body.get("question") or "").split())
    if len(question) < 8:
        raise ValueError("Say what the council should decide.")
    kind = body.get("kind") if body.get("kind") in DEBATE_KINDS else "other"
    product = (body.get("product") or "").strip() or None
    records.require_product(conn, product)
    tid = records.task_add(conn, f"Council debate: {question}"[:300], product=product,
                           description=f"Debate mode from the dashboard ({DEBATE_KINDS[kind]}).")
    path = paths.rel(records.task_workpath(conn, tid))
    brief = (
        f"Task T{tid}: council debate on {DEBATE_KINDS[kind]} for Urban Supplied"
        + (f", product {product}" if product else "") + f". The question: {question}\n\n"
        "Load the marketing-council skill and run it properly: each seat argues its own position from the evidence, "
        "then the council weighs them and gives one recommendation with the main dissent. Before you start, read what "
        "the brain holds (bin/jarvis mem search, the product's test records with bin/jarvis test show, the feedback "
        "digest) and Blake's method for this kind of call (blake-testing, blake-product-research, blake-mindset). "
        "Mentor methods outrank the council; label the council's own view as a JARVIS recommendation. Use real "
        "figures only, and say insufficient data where that is the truth.\n\n"
        f"REQUIRED deliverable: {path} (the question, each seat's case, the evidence with record ids, the recommendation, "
        "the dissent, what would change the call). Then record the outcome with bin/jarvis decide '<recommendation>' "
        f"--why '<the deciding reasons>' --task T{tid} --by jarvis" + (f" -p {product}" if product else "") + ", close the task "
        f"with bin/jarvis task done T{tid} --summary '...' --deliverable {path}, and send the owner two lines with "
        "bin/jarvis telegram send. Do not spend, launch, change budgets or edit the store: if the call needs one of "
        "those, raise it with bin/jarvis approval request and stop there."
    )
    run = work.start(brief, task=f"T{tid}", model=str(body.get("model") or ""))
    records.task_update(conn, tid, status="in_progress", note=f"W{run['id']} started the debate")
    return {"ok": True, "task": f"T{tid}", "worker": f"W{run['id']}", "deliverable": path}


# ---------------------------------------------------------------- HTTP

def page_token() -> str:
    """The key pages send with every POST. It's kept across restarts, so an open window keeps working."""
    path = paths.home() / "secrets" / "dashboard_token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(24)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    return token


def make_server(host="127.0.0.1", port=8765) -> ThreadingHTTPServer:
    hub = Hub()
    runner = Runner(hub)
    kokoro = tts.Kokoro()
    def heard(text: str) -> None:
        """The owner spoke a request (after "Hey JARVIS", the mic button or a follow-up): send it to JARVIS."""
        text = text.strip()
        if not text:
            return
        hub.publish({"type": "owner", "text": text, "via": "voice"})
        answered = runner.permissions.voice(text)  # "yes" / "no" while JARVIS waits for a permission
        if answered:
            hub.publish({"type": "notice", "text": f"A{answered['approval']} {answered['status']}"})
            return
        try:
            runner.start(text, queue=True)
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            hub.publish({"type": "error", "text": str(exc)})

    wake = wakeword.WakeWord(lambda line: hub.publish({"type": "wake", "detail": line}), heard,
                             lambda state: hub.publish({"type": "ears", "state": state}))
    phone = telegram.Bridge(hub, runner, kokoro)
    # Permission prompts also go to the phone with Approve / Reject buttons, when Telegram is set up.
    runner.permissions.push = lambda row: telegram.push_approval(row) if phone.enabled() else None
    token = page_token()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console quiet
            pass

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding: only requests addressed to this machine by loopback name are served.
            port_ = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_}", f"localhost:{port_}")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "forbidden host"})
            route, _, query = self.path.partition("?")
            first = route.strip("/").split("/", 1)[0]
            if route == "/" or first in APP_PAGES:
                html = (STATIC / SHELL).read_text(encoding="utf-8").replace("__JARVIS_TOKEN__", token)
                return self._send(200, live.inject(html).encode(), "text/html; charset=utf-8")
            if route in RETIRED:
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if ASSET_NAME.match(route) and (STATIC / route[1:]).is_file():
                return self._send(200, (STATIC / route[1:]).read_bytes(), ASSET_TYPES[route[route.rfind("."):]])
            if route == "/api/app":  # the whole state in one answer; after that the event stream carries the changes
                return self._json(200, {"sections": self.server.feed.snapshot(0 if "fresh" in query else 1.0)})
            if route.startswith("/api/control/"):  # the control centre (T98)
                found = control.handle_get(route, parse_qs(query), self.server)
                if found:
                    code, data, kind = found
                    return self._json(code, data) if kind == "json" else self._send(code, data, kind)
            if route.startswith("/api/ops/"):  # projects, sales, social, routines (T101)
                from . import ops
                found = ops.handle_get(route)
                if found:
                    return self._json(*found)
            if route == "/api/live":
                return self._json(200, live.state())
            if route == "/api/teammap":  # the team page: departments, each agent's skills, procedure and tasks
                from . import orgchart
                conn = db.connect()
                try:
                    return self._json(200, orgchart.state(conn))
                finally:
                    conn.close()
            if route == "/api/swarm/file":
                found = swarm.image((parse_qs(query).get("path") or [""])[0])
                return self._send(200, *found) if found else self._send(204, b"", "text/plain")
            if route == "/api/memory":
                return self._memory(parse_qs(query))
            if route == "/api/file":  # a deliverable's preview: brain files only, never secrets
                found = roster.preview((parse_qs(query).get("path") or [""])[0])
                # A deliverable that moved is normal, not an error the page should log: 204, nothing to show.
                return self._send(200, *found) if found else self._send(204, b"", "text/plain")
            if route == "/api/record":  # one record by id, for the record drawer
                conn = db.connect()
                try:
                    found = lookup.detail(conn, (parse_qs(query).get("id") or [""])[0], {a["name"]: a for a in agent_roster()})
                finally:
                    conn.close()
                return self._json(200, found) if found else self._json(404, {"error": "no such record"})
            if route.startswith("/api/memory/"):
                conn = db.connect()
                try:
                    entry = memory.get(conn, unquote(route.rsplit("/", 1)[1]))
                except memory.MemoryError as exc:
                    return self._json(404, {"error": str(exc)})
                finally:
                    conn.close()
                return self._json(200, {"meta": entry.meta, "body": entry.body, "path": paths.rel(entry.path)})
            if self.path == "/api/events":
                return self._events()
            if route == "/api/skills":  # per agent: skills and level, recently learned, backlog, review queue
                conn = db.connect()
                try:
                    agent = (parse_qs(query).get("agent") or [None])[0]
                    return self._json(200, skills.board(conn, agent))
                except skills.SkillError as exc:
                    return self._json(404, {"error": str(exc)})
                finally:
                    conn.close()
            self._json(404, {"error": "not found"})

        def _memory(self, q: dict):
            text = (q.get("q") or [""])[0].strip()
            cat = (q.get("c") or [None])[0] or None
            conn = db.connect()
            try:
                rows = (memory.search(conn, text, category=cat, limit=40) if text
                        else memory.listing(conn, category=cat, limit=200))
            finally:
                conn.close()
            keep = ("id", "title", "category", "product", "provenance", "status", "confidence", "source",
                    "source_ref", "updated", "snippet")
            return self._json(200, [{k: r[k] for k in keep if k in r.keys()} for r in rows])

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            feed = self.server.feed
            q = hub.subscribe()
            last_beat = time.time()
            try:
                # The whole state first, so the page draws from one message; after that the feed publishes only
                # the sections that changed, to every window at once.
                self._event({"type": "state", "full": True, "sections": feed.snapshot()})
                feed.start()
                while True:
                    try:
                        self._event(q.get(timeout=1.0))
                        while not q.empty():
                            self._event(q.get_nowait())
                    except queue.Empty:
                        pass
                    if time.time() - last_beat > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_beat = time.time()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                hub.unsubscribe(q)

        def _event(self, data: dict) -> None:
            self.wfile.write(f"data: {json.dumps(data, default=str)}\n\n".encode())
            self.wfile.flush()

        def do_POST(self):
            # Read the body first so a rejected request doesn't leave bytes on a kept-alive connection.
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                return self._json(413, {"error": "That's too large to send (images must be under 5 MB each)."})
            raw = self.rfile.read(length)
            if not self._host_ok() or self.headers.get("X-Jarvis-Token") != token:
                return self._json(403, {"error": "forbidden"})
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if self.path == "/api/chat":
                text = (body.get("text") or "").strip()
                images = body.get("images") or []
                files = body.get("files") or []
                if not text and not images and not files:
                    return self._json(400, {"error": "empty message"})
                if text and not images and not files:
                    answered = runner.permissions.voice(text)  # a typed "yes" / "no" answers a waiting prompt
                    if answered:
                        return self._json(200, {"ok": True, "answered": answered})
                try:
                    how = runner.start(text, images if isinstance(images, list) else [], files if isinstance(files, list) else [],
                                       queue=body.get("queue", True) is not False)
                except RuntimeError as exc:
                    return self._json(409, {"error": str(exc)})
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                except FileNotFoundError as exc:
                    return self._json(503, {"error": str(exc)})
                return self._json(202, {"ok": True, "queued": how == "queued", "waiting": len(runner.queue)})
            if self.path == "/api/tts":
                try:
                    wav = kokoro.speak(body.get("text") or "", body.get("voice"), body.get("speed"))
                except (tts.TTSError, OSError, ValueError) as exc:
                    return self._json(503, {"error": str(exc)})
                return self._send(200, wav, "audio/wav")
            if self.path == "/api/wake":
                # {"on": bool} switches "Hey JARVIS" on/off (remembered in brain/config.json);
                # {"paused": bool} ignores the mic while JARVIS is speaking or already listening.
                if "on" in body:
                    paths.set_config("wake_word", bool(body["on"]))
                    wake.start() if body["on"] else wake.stop()
                if "paused" in body:
                    wake.send("pause" if body["paused"] else "resume")
                return self._json(200, {**wake.info(), "enabled": wake.enabled()})
            if self.path == "/api/listen":
                # Record one request through the local ears (no wake word): the mic button, or a
                # follow-up in a conversation. 409 tells the page to use the browser's recogniser.
                if body.get("cancel"):
                    wake.send("cancel")
                    return self._json(200, {"ok": True})
                if not wake.listen(bool(body.get("followup"))):
                    return self._json(409, {"error": "local speech recognition isn't running"})
                return self._json(202, {"ok": True})
            if self.path == "/api/approval":
                conn = db.connect()
                try:
                    row = records.approval_decide(conn, body.get("id"), bool(body.get("approve")), body.get("note"))
                except (records.RecordError, ValueError, TypeError) as exc:
                    return self._json(400, {"error": str(exc)})
                finally:
                    conn.close()
                phone.decided(row, via="dashboard")  # approved: JARVIS carries it out now
                return self._json(200, {"ok": True, "status": row["status"]})
            if self.path == "/api/connectors":   # ask Claude Code which connectors answer (it takes a while)
                return self._json(202, roster.check_connectors())
            if self.path == "/api/conversation":
                # The model the conversation runs on (fast for talking, the strongest for hard work), kept in
                # brain/config.json; the running Claude Code is closed so the next message uses it.
                if "model" in body:
                    model = (body.get("model") or "").strip().lower()
                    if model not in ("", "haiku", "sonnet", "opus", "fable"):
                        return self._json(400, {"error": "Pick haiku, sonnet, opus, fable or the default."})
                    paths.set_config("dashboard_model", roster.MODEL_IDS.get(model, model) or None)
                if "effort" in body:
                    effort = (body.get("effort") or "").strip().lower()
                    if effort not in ("", "low", "medium", "high"):
                        return self._json(400, {"error": "Pick low, medium, high or the default."})
                    paths.set_config("dashboard_effort", effort or None)
                if not runner.busy:
                    runner.close(); runner.warm()
                cfg = paths.config()
                return self._json(200, {"ok": True, "model": cfg.get("dashboard_model"), "effort": cfg.get("dashboard_effort")})
            if self.path in ("/api/task", "/api/agent-model", "/api/task-model"):
                # From the page: a new task for the backlog, an agent's model, a model for one task.
                conn = db.connect()
                try:
                    if self.path == "/api/task":
                        tid = roster.add_task(conn, body.get("title"), agent=body.get("agent"), product=body.get("product"),
                                              description=body.get("description"), model=body.get("model"))
                        return self._json(201, {"ok": True, "id": tid})
                    if self.path == "/api/agent-model":
                        return self._json(200, {"ok": True, "model": roster.set_agent_model(body.get("agent"), body.get("model"))})
                    return self._json(200, {"ok": True, "task_models": roster.set_task_model(body.get("task"), body.get("model"))})
                except (ValueError, records.RecordError) as exc:
                    return self._json(400, {"error": str(exc)})
                finally:
                    conn.close()
            if self.path == "/api/debate":   # a big call argued out by the marketing council, as a durable worker
                from . import work
                conn = db.connect()
                try:
                    return self._json(201, start_debate(conn, body))
                except (ValueError, records.RecordError, work.WorkError, OSError) as exc:
                    return self._json(400, {"error": str(exc)})
                finally:
                    conn.close()
            if self.path.startswith("/api/control/"):
                found = control.handle_post(self.path, body, self.server)
                if found:
                    return self._json(*found)
            if self.path == "/api/stop":
                runner.stop()
                return self._json(200, {"ok": True})
            if self.path == "/api/new":
                if runner.busy:
                    return self._json(409, {"error": "JARVIS is still answering."})
                runner.reset()
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        # On Windows SO_REUSEADDR lets a second dashboard bind the same port silently and serve
        # stale code; fail instead so serve() can open the one already running.
        allow_reuse_address = os.name != "nt"

        def shutdown(self):
            self.feed.close()
            self.runner.closed = True
            super().shutdown()

        def handle_error(self, request, client_address):
            import sys
            if not isinstance(sys.exc_info()[1], ConnectionError):  # a closed tab is not an error
                super().handle_error(request, client_address)

    server = Server((host, port), Handler)
    server.hub, server.runner, server.token, server.tts, server.wake = hub, runner, token, kokoro, wake
    server.telegram = phone
    server.feed = cockpit.Feed(server)
    server.keep_warm = False   # serve() turns it on: Claude Code is started again whenever it has ended
    return server


def serve(port: int = 8765, open_browser: bool = True) -> None:
    restarted = os.environ.pop("JARVIS_RESTARTED", "")
    for _ in range(30 if restarted else 1):  # after a live restart, wait for the old copy to let go of the port
        try:
            server = make_server(port=port)
            break
        except OSError:
            if restarted:
                time.sleep(0.5)
    else:
        if restarted:
            sys.stderr.write("jarvis: restart couldn't take over the port\n")
            return
        url = f"http://127.0.0.1:{port}/"
        print(f"JARVIS dashboard is already running at {url}. Opening it.", flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    # Warm up for the first spoken turn: the voice model loads and Claude Code starts before anyone says a word.
    threading.Thread(target=server.tts.warm, daemon=True).start()
    server.runner.prime = True
    server.runner.warm()
    server.keep_warm = True
    server.feed.start()   # it also restarts Claude Code if it ends while idle, so a reply never waits for a cold start
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"JARVIS dashboard running at {url}  (Ctrl+C to stop)", flush=True)
    if not find_claude():
        print("Note: Claude Code CLI not found, so the page will show live state but can't talk to JARVIS yet.",
              flush=True)
    if server.tts.command():
        threading.Thread(target=server.tts.warm, daemon=True).start()
    else:
        print("Note: Kokoro voice not installed, so JARVIS will use the browser's voice.", flush=True)
    if server.wake.enabled():
        server.wake.start()
    if server.telegram.enabled():
        server.telegram.start()  # the owner's phone: texts, voice notes and photos to JARVIS
    if open_browser and not restarted:
        webbrowser.open(url)
    swap = threading.Event()
    def restart() -> None:
        swap.set()
        server.shutdown()
    watcher = live.Watcher(lambda: server.runner.busy, restart, server.hub.publish)
    watcher.start()  # new Python code is picked up between replies, without reopening the app
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        server.wake.stop()
        server.telegram.stop()
        server.runner.close()
    if swap.is_set():
        server.server_close()
        live.respawn()
        os._exit(0)  # leftover threads (open event streams) mustn't keep the old copy alive
        server.tts.stop()
        server.server_close()
