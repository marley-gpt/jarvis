"""Telegram: the owner texts, voice-notes or sends photos to JARVIS from their phone.

The bot token lives in brain/secrets/telegram.json (never in the repo or the journal).
The dashboard server runs the bridge: it long-polls the Bot API, passes the owner's
messages to the same JARVIS conversation the dashboard uses (so they show in the chat
app too), and sends JARVIS's replies back. A voice note is transcribed locally and
answered with a voice note in JARVIS's voice as well as text. Photos go to JARVIS as images.

Only the owner's chat is served. Until it is known, the bot answers nobody except a
message containing the pairing code from `bin/jarvis telegram pair`; that chat id is then
saved and every other chat is ignored.

`bin/jarvis telegram send '<text>' [--voice]` messages the owner from anywhere (briefs).
`bin/jarvis alert '<text>' [--urgent]` is for problems: urgent ones also come as a voice note
and a phone call. Telegram bots can't call, so the call goes through CallMeBot (free), which
rings the owner's Telegram and reads a fixed line; the details stay in the chat.

Every approval request arrives with Approve / Reject buttons. Approving records it, opens a
short clearance window (records.grant_open) and tells JARVIS to carry it out.

The chat has a button keyboard: Today (answered from the brain, instantly, no Claude), Brief me,
Check the store and New product. Commands: /today /brief /store /product /stop /new.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from urllib.parse import urlencode

from . import db, jobs, media, paths, records

API = "https://api.telegram.org"
CALLMEBOT = "https://api.callmebot.com/start.php"
PREFIX = "(Sent from the owner's phone by Telegram; reply in a few short sentences.) "
# Only this line goes to the call service; what happened is in the Telegram message.
CALL_LINE = "Sir, JARVIS here. Something urgent needs you. The details are in Telegram."
STORE_CHECK_SECONDS = 900

# One-tap actions, shared with the chat app.
ACTIONS = {
    "brief": "Brief me now: the jarvis-daily-brief, short, with real numbers.",
    "store": "Check the store like a customer with the store-qa agent and tell me what's broken, most serious first.",
    "product": "Start the ads for this product (jarvis-product-pipeline): ",
}
HUNT_JOB = ("Product hunt: follow the desktop-jobs procedure in the master-key-tools skill, with Blake's method and the "
            "owner's product rules, then send the best one or two.")
BUTTONS = {"📋 Today": "/today", "🎙 Brief me": "/brief", "🛒 Check the store": "/store", "➕ New product": "/product",
           "🔎 Product hunt": "/hunt"}
KEYBOARD = {"keyboard": [[{"text": "📋 Today"}, {"text": "🎙 Brief me"}], [{"text": "🛒 Check the store"}, {"text": "➕ New product"}], [{"text": "🔎 Product hunt"}]],
            "resize_keyboard": True, "is_persistent": True}
COMMANDS = [("today", "Today at a glance"), ("brief", "Brief me now"), ("store", "Check the store"),
            ("product", "Start a new product"), ("hunt", "Product hunt on the PC"), ("jobs", "Jobs on the PC"), ("approvals", "Approvals waiting"), ("stop", "Interrupt JARVIS"), ("new", "Fresh conversation")]


class TelegramError(Exception):
    pass


def secrets_path() -> Path:
    return paths.home() / "secrets" / "telegram.json"


def settings() -> dict:
    try:
        return json.loads(secrets_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(**changes) -> dict:
    data = {**settings(), **changes}
    secrets_path().parent.mkdir(parents=True, exist_ok=True)
    secrets_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def pairing_code() -> str:
    code = settings().get("pairing_code")
    if not code:
        code = f"{secrets.randbelow(10**6):06d}"
        save(pairing_code=code)
    return code


def call(method: str, params: dict | None = None, file: tuple[str, str, bytes] | None = None, timeout: int = 70):
    """One Bot API call. `file` is (field, filename, bytes) for uploads such as sendVoice."""
    token = settings().get("bot_token")
    if not token:
        raise TelegramError("No Telegram bot token in brain/secrets/telegram.json.")
    url = f"{API}/bot{token}/{method}"
    if file:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (params or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        field, name, data = file
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(url, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, json.dumps(params or {}).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TelegramError(f"Telegram unreachable: {exc}") from exc
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram refused the request.")
    return body["result"]


def download(file_id: str) -> bytes:
    path = call("getFile", {"file_id": file_id})["file_path"]
    with urllib.request.urlopen(f"{API}/file/bot{settings()['bot_token']}/{path}", timeout=60) as r:
        return r.read()


def to_ogg(wav: bytes) -> bytes | None:
    """Telegram voice notes must be Opus in OGG; None if ffmpeg isn't available."""
    if not shutil.which("ffmpeg"):
        return None
    proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "40k", "-f", "ogg",
                           "pipe:1"], input=wav, capture_output=True, creationflags=paths.NO_WINDOW)
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def send(text: str, voice: bytes | None = None, chat_id=None, markup: dict | None = None) -> None:
    """Message the owner: text (split to Telegram's 4096-character limit) and optionally a voice note.
    `markup` (buttons or the keyboard) goes on the last part."""
    chat = chat_id or settings().get("chat_id")
    if not chat:
        raise TelegramError("Not paired yet: send the bot the code from `bin/jarvis telegram pair`.")
    if voice:
        call("sendVoice", {"chat_id": chat}, file=("voice", "jarvis.ogg", voice))
    parts = [text[i:i + 4000] for i in range(0, len(text or ""), 4000)]
    for n, part in enumerate(parts):
        params = {"chat_id": chat, "text": part}
        if markup and n == len(parts) - 1:
            params["reply_markup"] = markup
        call("sendMessage", params)


def buttons(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}


def push_approval(row, chat_id=None) -> None:
    """Send an approval request to the phone with Approve / Reject buttons."""
    amount = f" ({row['amount']:g} {row['currency'] or ''}".rstrip() + ")" if row["amount"] is not None else ""
    task = f" · task T{row['task_id']}" if row["task_id"] else ""
    text = f"Approval needed, A{row['id']} · {row['kind']}{amount}{task}\n\n{row['summary']}"
    if row["detail"]:
        text += f"\n\n{row['detail']}"
    send(text, chat_id=chat_id,
         markup=buttons([[("✅ Approve", f"ap:{row['id']}:y"), ("❌ Reject", f"ap:{row['id']}:n")]]))


def push_pending(conn, chat_id=None) -> int:
    """/approvals: every pending approval, each with its own buttons. Returns how many."""
    rows = conn.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id").fetchall()
    if not rows:
        send("Nothing waiting for your approval, sir.", chat_id=chat_id)
    for row in rows:
        push_approval(row, chat_id=chat_id)
    return len(rows)


def ring() -> str:
    """Ring the owner's Telegram through CallMeBot; it reads CALL_LINE aloud."""
    user = settings().get("call_user")
    if not user:
        raise TelegramError("No Telegram username saved for calls yet: the owner needs a username in Telegram's "
                            "settings, then any message to the bot saves it.")
    url = CALLMEBOT + "?" + urlencode({"user": "@" + user, "text": CALL_LINE, "lang": "en-GB-Standard-B", "rpt": 2})
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise TelegramError(f"The call service didn't answer: {exc}") from exc
    plain = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)  # the page's analytics code
    plain = re.sub(r"<[^>]+>", " ", plain)
    if re.search(r"not (yet )?(authori[sz]ed|allowed)|\berror\b|invalid", plain, re.I):
        raise TelegramError("The call service refused: " + " ".join(plain.split())[:200]
                            + " (the owner must allow it once by messaging @CallMeBot_txtbot /start)")
    return " ".join(plain.split())[:200]


def alert(text: str, urgent: bool = False, kokoro=None) -> list[str]:
    """Push a problem to the owner. Urgent: also a voice note and a phone call. Returns what was sent."""
    send(("🚨 URGENT: " if urgent else "⚠️ ") + text)
    sent = ["message"]
    if urgent:
        ogg = speak(kokoro, text) if kokoro else None
        if ogg:
            call("sendVoice", {"chat_id": settings().get("chat_id")}, file=("voice", "jarvis.ogg", ogg))
            sent.append("voice note")
        try:
            ring()
            sent.append("call")
        except TelegramError as exc:
            sent.append(f"no call: {exc}")
    return sent


def today(conn) -> dict:
    """Today at a glance, straight from the brain: approvals waiting, live tests, work in progress, AI spend."""
    tests = []
    for t in conn.execute("SELECT id, product, angle, status FROM tests WHERE status IN ('running','extended') ORDER BY id"):
        e = records.evaluate(conn, t["id"])
        tests.append({"id": f"X{t['id']}", "product": t["product"], "angle": t["angle"], "spend": e["spend"],
                      "purchases": e["purchases"], "revenue": e["revenue"], "roas": e["roas"], "days": e["days"],
                      "sufficient": e["sufficient"]})
    working = [dict(r) for r in conn.execute(
        "SELECT id, title, agent FROM tasks WHERE status = 'in_progress' ORDER BY updated DESC LIMIT 5")]
    ok, spent, budget = records.cost_check(conn, 0)
    return {
        "approvals": [dict(r) for r in conn.execute(
            "SELECT id, kind, summary, amount, currency FROM approvals WHERE status = 'pending' ORDER BY id")],
        "tests": tests,
        "working": working,
        "open_tasks": conn.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({','.join('?' * len(records.OPEN_TASK_STATUSES))})",
                                   records.OPEN_TASK_STATUSES).fetchone()[0],
        "ai_spend": {"spent": round(spent, 2), "budget": budget},
    }


def today_text(d: dict) -> str:
    lines = ["Today, sir:"]
    if d["approvals"]:
        lines.append(f"• {len(d['approvals'])} waiting for you: " + "; ".join(f"A{a['id']} {a['summary']}" for a in d["approvals"]))
    else:
        lines.append("• Nothing waiting for your approval.")
    if d["tests"]:
        for t in d["tests"]:
            roas = f", ROAS {t['roas']:.2f}" if t["roas"] else ""
            note = "" if t["sufficient"] else " (not enough data yet)"
            lines.append(f"• Test {t['id']} {t['product']}: ${t['spend']:.2f} spent, {t['purchases']} sales over "
                         f"{t['days']} days{roas}{note}")
    else:
        lines.append("• No test running.")
    if d["working"]:
        lines.append("• Working on: " + "; ".join(f"T{w['id']} {w['title']}" for w in d["working"]))
    lines.append(f"• {d['open_tasks']} open tasks. AI spend A${d['ai_spend']['spent']:.2f} of A${d['ai_spend']['budget']:.0f}.")
    return "\n".join(lines)


def speak(kokoro, text: str) -> bytes | None:
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links read as their words
    plain = re.sub(r"[*_#`>|]", "", plain)
    try:
        return to_ogg(kokoro.speak(plain))
    except Exception:  # voice is a bonus; the text still goes
        return None


MAX_DOWNLOAD = 20 * 1024 * 1024  # the Bot API's getFile limit


def archive(msg: dict) -> None:
    """Every message the owner sends is kept, before anything else happens to it: brain/telegram/<day>.jsonl."""
    folder = paths.home() / "telegram"
    folder.mkdir(parents=True, exist_ok=True)
    sent = msg.get("date") or time.time()
    kinds = [k for k in ("text", "caption", "voice", "audio", "photo", "video", "video_note", "animation", "document",
                         "sticker", "location", "contact") if msg.get(k)]
    line = {"sent": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sent)), "received": paths.now(),
            "kinds": kinds, "text": msg.get("text") or msg.get("caption") or "", "forwarded": forwarded(msg) or None,
            "message_id": msg.get("message_id")}
    with open(folder / f"{time.strftime('%Y-%m-%d', time.localtime(sent))}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def forwarded(msg: dict) -> str:
    """Who a forwarded message came from, so JARVIS knows it isn't the owner's own words."""
    origin = msg.get("forward_origin") or {}
    who = (origin.get("sender_user") or {}).get("first_name") or origin.get("sender_user_name")         or (origin.get("chat") or {}).get("title") or (msg.get("forward_from") or {}).get("first_name")
    if not (origin or msg.get("forward_from") or msg.get("forward_from_chat")):
        return ""
    return f"(Forwarded by the owner, originally from {who or 'someone else'}.)"


def video_frames(path: Path, count: int = 3) -> list[dict]:
    """A few frames from a video as JPEG images for JARVIS to look at (empty if ffmpeg isn't available)."""
    if not shutil.which("ffmpeg"):
        return []
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                               capture_output=True, text=True, creationflags=paths.NO_WINDOW)
        duration = float(probe.stdout.strip() or 0)
    except (OSError, ValueError):
        duration = 0
    frames = []
    for i in range(count):
        at = duration * (i + 0.5) / count if duration else i
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                               "-vf", "scale=720:-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                              capture_output=True, creationflags=paths.NO_WINDOW)
        if proc.returncode == 0 and proc.stdout:
            frames.append({"media_type": "image/jpeg", "data": base64.b64encode(proc.stdout).decode()})
    return frames


class Bridge:
    """Long-polls Telegram inside the dashboard server and relays to and from JARVIS.

    Also watches the store (an alert if it stops answering) and carries out approvals
    decided by button, here or in the chat app.
    """

    def __init__(self, hub, runner, kokoro):
        self.hub, self.runner, self.kokoro = hub, runner, kokoro
        self.running = False
        self.waiting = False      # a reply is owed to the phone
        self.voice_reply = False  # the owner sent a voice note, so answer with one too
        self.said: list[str] = []
        self.error: str | None = None
        self.queued: str | None = None  # an approved action to start once JARVIS is free
        self.pending: list[dict] = []   # owner messages that arrived while JARVIS was busy, answered next in order
        self.next_prefix = ""           # set by "New product": the next message is the product
        self.store_failures = 0

    def enabled(self) -> bool:
        return bool(settings().get("bot_token"))

    def info(self) -> dict:
        s = settings()
        return {"configured": bool(s.get("bot_token")), "paired": bool(s.get("chat_id")), "running": self.running,
                "calls": bool(s.get("call_user")), "error": self.error}

    def start(self) -> None:
        if self.running or not self.enabled():
            return
        self.running = True
        try:
            call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]}, timeout=20)
        except TelegramError as exc:
            self.error = str(exc)
        threading.Thread(target=self._poll, daemon=True).start()
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._watch_store, daemon=True).start()

    def stop(self) -> None:
        self.running = False

    def _poll(self) -> None:
        offset = None
        while self.running:
            try:
                updates = call("getUpdates", {"timeout": 50, "offset": offset,
                                              "allowed_updates": ["message", "callback_query"]})
                self.error = None
            except TelegramError as exc:
                self.error = str(exc)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    if u.get("callback_query"):
                        self._button(u["callback_query"])
                    else:
                        self._message(u.get("message") or {})
                except Exception as exc:  # one bad message must not stop the bridge
                    self.error = f"{type(exc).__name__}: {exc}"

    def _reply(self, chat, text: str, markup: dict | None = None) -> None:
        if not chat:
            return
        try:
            send(text, chat_id=chat, markup=markup)
        except TelegramError as exc:
            self.error = str(exc)

    def _message(self, msg: dict) -> None:
        chat = (msg.get("chat") or {}).get("id")
        if not chat:
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        owner = settings().get("chat_id")
        if not owner:
            if pairing_code() in text:
                save(chat_id=chat, pairing_code=None)
                self._reply(chat, "Paired. Good to meet you here, sir. Text me, send a voice note or a photo, "
                                  "or use the buttons below. /stop interrupts me, /new starts a fresh conversation.",
                            KEYBOARD)
            else:
                self._reply(chat, "This is a private assistant. Send the pairing code to connect.")
            return
        if chat != owner:
            return  # strangers are ignored
        archive(msg)
        late = time.time() - (msg.get("date") or time.time())
        if late > 300 and text:  # sent while the PC or JARVIS was off; Telegram held it
            text = f"(Sent at {time.strftime('%H:%M', time.localtime(msg['date']))}, while I was offline.) {text}"
        username = (msg.get("from") or {}).get("username")
        if username and username != settings().get("call_user"):
            save(call_user=username)  # needed to ring the owner for urgent alerts
        text = BUTTONS.get(text, text)
        if text in ("/start", "/help"):
            return self._reply(chat, "At your service, sir. Text, voice note or photo, or a button below. "
                                     "/stop interrupts me, /new starts fresh.", KEYBOARD)
        if text == "/today":
            conn = db.connect()
            try:
                return self._reply(chat, today_text(today(conn)), KEYBOARD)
            finally:
                conn.close()
        if text == "/approvals":
            conn = db.connect()
            try:
                push_pending(conn, chat_id=chat)
            except TelegramError as exc:
                self.error = str(exc)
            finally:
                conn.close()
            return
        if text == "/stop":
            self.runner.stop()
            return self._reply(chat, "Stopped.")
        if text == "/new":
            if self.runner.busy:
                return self._reply(chat, "I'm mid-task, sir. Send /stop first.")
            self.runner.reset()
            return self._reply(chat, "Fresh conversation. Memory is kept.")
        if text == "/hunt":
            conn = db.connect()
            try:
                jid = jobs.add(conn, HUNT_JOB, "phone")
            finally:
                conn.close()
            return self._reply(chat, f"Queued as J{jid}, sir. I'll text you the results." if jobs.runner_alive() else
                               f"Queued as J{jid}, sir, but the runner on the PC isn't running, so it won't start until "
                               "it's opened again.", KEYBOARD)
        if text == "/jobs":
            conn = db.connect()
            try:
                rows = jobs.listing(conn, 6)
            finally:
                conn.close()
            lines = [f"The PC runner is {'on' if jobs.runner_alive() else 'off'}."]
            lines += [f"J{r['id']} {r['status']}: {r['text'][:70]}" for r in rows]
            return self._reply(chat, "\n".join(lines))
        if text == "/product":
            self.next_prefix = ACTIONS["product"]
            return self._reply(chat, "Send me the product: a link or a photo, with a line about it.")
        if text in ("/brief", "/store"):
            return self._ask(chat, ACTIONS[text[1:]], shown={"/brief": "Brief me", "/store": "Check the store"}[text])
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            with tempfile.TemporaryDirectory() as tmp:
                note = Path(tmp) / "note.ogg"
                note.write_bytes(download(voice["file_id"]))
                try:
                    heard = media.transcribe(note)
                except media.MediaError as exc:
                    return self._reply(chat, str(exc))
            if not heard:
                return self._reply(chat, "I couldn't make out that voice note, sir.")
            text = (text + " " + heard).strip()
        images = []
        if msg.get("photo"):
            biggest = max(msg["photo"], key=lambda p: p.get("file_size") or 0)
            images.append({"media_type": "image/jpeg", "data": base64.b64encode(download(biggest["file_id"])).decode()})
        note, more = self._attachment(chat, msg)
        images += more
        text = "\n\n".join(filter(None, [forwarded(msg), text, note]))
        if not text and not images:
            return
        prefix, self.next_prefix = self.next_prefix, ""
        self._ask(chat, prefix + text, images, voice=bool(voice), shown=text or "(photo)")

    def _attachment(self, chat, msg: dict) -> tuple[str, list]:
        """Videos, video messages, GIFs and files: saved to brain/files/telegram/<day>/. A video's speech is
        transcribed and three frames go to JARVIS as images; an image sent as a file goes as an image."""
        kind = next((k for k in ("video", "video_note", "animation", "document") if msg.get(k)), None)
        if not kind:
            return "", []
        item = msg[kind]
        if (item.get("file_size") or 0) > MAX_DOWNLOAD:
            self._reply(chat, "That file is over Telegram's 20 MB limit for bots, sir. Send me a link to it instead.")
            return f"(The owner sent a {kind} too large to download: {item.get('file_name') or 'no name'}.)", []
        mime = item.get("mime_type") or ("video/mp4" if kind != "document" else "")
        name = item.get("file_name") or f"{kind}-{msg.get('message_id', int(time.time()))}" + (".mp4" if "video" in mime or kind != "document" else "")
        folder = paths.files_dir() / "telegram" / paths.now()[:10]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / re.sub(r"[^\w.-]+", "_", name)
        path.write_bytes(download(item["file_id"]))
        if mime.startswith("image/") and mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            return f"(Image file saved at {paths.rel(path)}.)", [{"media_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}]
        if not mime.startswith("video/"):
            return f"(The owner sent a file, saved at {paths.rel(path)}. Read it.)", []
        try:
            heard = media.transcribe(path)
        except media.MediaError:
            heard = ""
        frames = video_frames(path)
        return (f"(The owner sent a video, saved at {paths.rel(path)}. Frames attached above. "
                f"What's said in it: {heard or 'no speech'})"), frames

    def _ask(self, chat, text: str, images=None, voice=False, shown=None) -> None:
        """Pass a request to JARVIS; the answer comes back to this chat. Never dropped: if JARVIS is busy
        (or a burst arrives after the PC was off), it waits its turn and is answered in order."""
        if self.runner.busy:
            self.pending.append({"text": text, "images": images or [], "voice": voice, "shown": shown or text})
            n = len(self.pending)
            return self._reply(chat, "Noted, sir. I'm finishing something first; this is next."
                               if n == 1 else f"Noted, sir. {n} messages waiting; I'll take them in order.")
        self.waiting, self.voice_reply, self.said = True, voice, []
        self.hub.publish({"type": "owner", "text": shown or text, "via": "telegram"})
        try:
            self.runner.start(PREFIX + text, images or [])
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            self.waiting = False
            self._reply(chat, f"I can't do that right now: {exc}")

    def _ask_pending(self, chat) -> None:
        batch, self.pending = self.pending, []
        if len(batch) == 1:
            b = batch[0]
            return self._ask(chat, b["text"], b["images"], b["voice"], b["shown"])
        lines = [f"{i}. {b['text']}" for i, b in enumerate(batch, 1)]
        text = (f"While I was busy you sent these {len(batch)} messages. Go through them in order, answer each one "
                "by its number, and save anything worth remembering to memory:\n\n" + "\n\n".join(lines))
        images = [img for b in batch for img in b["images"]][:4]
        self._ask(chat, text, images, any(b["voice"] for b in batch), shown=f"{len(batch)} messages")

    def _button(self, cq: dict) -> None:
        msg = cq.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        if not chat or chat != settings().get("chat_id"):
            return
        kind, _, rest = (cq.get("data") or "").partition(":")
        if kind != "ap":
            return
        aid, _, verdict = rest.partition(":")
        if not aid.isdigit() or verdict not in ("y", "n"):
            return
        conn = db.connect()
        try:
            row = records.approval_decide(conn, aid, verdict == "y", "decided on the phone")
            note = "✅ Approved. On it." if verdict == "y" else "❌ Rejected. I won't do it."
        except records.RecordError as exc:  # already decided elsewhere, or gone
            row, note = None, f"ℹ️ No change: {exc}."
        finally:
            conn.close()
        try:
            call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": note[:190]}, timeout=20)
            call("editMessageText", {"chat_id": chat, "message_id": msg.get("message_id"),
                                     "text": (msg.get("text") or "") + "\n\n" + note}, timeout=20)
        except TelegramError as exc:
            self.error = str(exc)
        if row:
            self.decided(row, via="phone")

    def decided(self, row, via: str = "dashboard") -> None:
        """An approval was decided: refresh the screens and, if approved, have JARVIS carry it out."""
        self.hub.publish({"type": "approval", "id": row["id"], "status": row["status"]})
        if row["status"] != "approved" or row["kind"] == "tool":
            return  # a tool approval: the session that asked is waiting for it and carries on by itself
        text = (f"I approved A{row['id']} ({row['kind']}: {row['summary']}) on the {via}. It's recorded and you're "
                f"cleared for {records.GRANT_MINUTES} minutes. Carry it out now, log it, and tell me when it's done.")
        if self.runner.busy:
            self.queued = text
            if via == "phone":
                self._reply(settings().get("chat_id"), "I'll do it as soon as I finish the current job, sir.")
        elif via == "phone":
            self._ask(settings().get("chat_id"), text, shown=f"Approved A{row['id']}")
        else:
            try:
                self.runner.start(text)
            except (RuntimeError, FileNotFoundError, ValueError) as exc:
                self.hub.publish({"type": "error", "text": f"Couldn't start A{row['id']}: {exc}"})

    def _watch_store(self) -> None:
        """Alert the owner if the store stops answering (two checks in a row), and when it's back."""
        while self.running:
            url = paths.config().get("store_url")
            if url:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JARVIS store check"})
                    with urllib.request.urlopen(req, timeout=30):
                        up = True
                except urllib.error.HTTPError as exc:
                    up = exc.code < 500  # 403/429 is Shopify's bot check: the store is answering
                except (urllib.error.URLError, OSError):
                    up = False
                if up and self.store_failures >= 2:
                    self._safe_alert(f"The store is back up ({url}).")
                self.store_failures = 0 if up else self.store_failures + 1
                if self.store_failures == 2:
                    self._safe_alert(f"The store isn't loading ({url}). Customers can't buy right now.", urgent=True)
            for _ in range(STORE_CHECK_SECONDS):
                if not self.running:
                    return
                time.sleep(1)

    def _safe_alert(self, text: str, urgent: bool = False) -> None:
        try:
            alert(text, urgent, self.kokoro)
        except TelegramError as exc:
            self.error = str(exc)

    def _listen(self) -> None:
        q = self.hub.subscribe()
        try:
            while self.running:
                try:
                    ev = q.get(timeout=1)
                except Exception:
                    continue
                kind, owner = ev.get("type"), settings().get("chat_id")
                idle = kind == "status" and ev.get("status") == "idle"
                if self.waiting:
                    if kind == "say" and ev.get("text"):
                        self.said.append(ev["text"])
                    elif kind == "reply" and ev.get("text") and (ev.get("is_error") or not self.said):
                        self.said.append(ev["text"])
                    elif kind == "dispatch":  # long jobs: say who's on it now, the answer comes at the end
                        self._reply(owner, f"On it: the {ev.get('agent')} agent is working on {ev.get('description') or 'it'}.")
                    elif kind == "error":
                        self.said.append("Problem: " + ev.get("text", "something went wrong."))
                    elif idle:
                        self.waiting = False
                        if self.said:  # one message with the whole answer
                            self._reply(owner, "\n\n".join(self.said))
                        if self.voice_reply and self.said:
                            ogg = speak(self.kokoro, self.said[-1][:600])
                            if ogg:
                                try:
                                    call("sendVoice", {"chat_id": owner}, file=("voice", "jarvis.ogg", ogg))
                                except TelegramError as exc:
                                    self.error = str(exc)
                if idle and self.queued and not self.runner.busy:
                    text, self.queued = self.queued, None
                    self._ask(owner, text, shown="Carrying out the approval")
                elif idle and self.pending and not self.runner.busy:
                    self._ask_pending(owner)
        finally:
            self.hub.unsubscribe(q)
