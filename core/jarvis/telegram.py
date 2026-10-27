"""Telegram: the owner texts, voice-notes or sends photos to JARVIS from their phone.

The bot token lives in brain/secrets/telegram.json (never in the repo or the journal).
The dashboard server runs the bridge: it long-polls the Bot API, passes the owner's
messages to the same JARVIS conversation the dashboard uses (so they show in the chat
app too), and sends JARVIS's replies back. A voice note is transcribed locally and
answered with a voice note in JARVIS's voice as well as text. Photos go to JARVIS as images.

Only the owner's chat is served. Until it is known, the bot answers nobody except a
message containing the pairing code from `bin/jarvis telegram pair`; that chat id is then
saved and every other chat is ignored.

`bin/jarvis telegram send '<text>' [--voice]` messages the owner from anywhere (briefs,
approvals, alerts). Commands in the chat: /stop interrupts JARVIS, /new starts a fresh
conversation. Telegram bots can't place calls, so urgent things come as a voice note.
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

from . import media, paths

API = "https://api.telegram.org"
PREFIX = "(Sent from the owner's phone by Telegram; reply in a few short sentences.) "


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


def send(text: str, voice: bytes | None = None, chat_id=None) -> None:
    """Message the owner: text (split to Telegram's 4096-character limit) and optionally a voice note."""
    chat = chat_id or settings().get("chat_id")
    if not chat:
        raise TelegramError("Not paired yet: send the bot the code from `bin/jarvis telegram pair`.")
    if voice:
        call("sendVoice", {"chat_id": chat}, file=("voice", "jarvis.ogg", voice))
    for i in range(0, len(text or ""), 4000):
        call("sendMessage", {"chat_id": chat, "text": text[i:i + 4000]})


def speak(kokoro, text: str) -> bytes | None:
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links read as their words
    plain = re.sub(r"[*_#`>|]", "", plain)
    try:
        return to_ogg(kokoro.speak(plain))
    except Exception:  # voice is a bonus; the text still goes
        return None


class Bridge:
    """Long-polls Telegram inside the dashboard server and relays to and from JARVIS."""

    def __init__(self, hub, runner, kokoro):
        self.hub, self.runner, self.kokoro = hub, runner, kokoro
        self.running = False
        self.waiting = False      # a reply is owed to the phone
        self.voice_reply = False  # the owner sent a voice note, so answer with one too
        self.said: list[str] = []
        self.error: str | None = None

    def enabled(self) -> bool:
        return bool(settings().get("bot_token"))

    def info(self) -> dict:
        s = settings()
        return {"configured": bool(s.get("bot_token")), "paired": bool(s.get("chat_id")), "running": self.running,
                "error": self.error}

    def start(self) -> None:
        if self.running or not self.enabled():
            return
        self.running = True
        threading.Thread(target=self._poll, daemon=True).start()
        threading.Thread(target=self._listen, daemon=True).start()

    def stop(self) -> None:
        self.running = False

    def _poll(self) -> None:
        offset = None
        while self.running:
            try:
                updates = call("getUpdates", {"timeout": 50, "offset": offset, "allowed_updates": ["message"]})
                self.error = None
            except TelegramError as exc:
                self.error = str(exc)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self._message(u.get("message") or {})
                except Exception as exc:  # one bad message must not stop the bridge
                    self.error = f"{type(exc).__name__}: {exc}"

    def _reply(self, chat, text: str) -> None:
        try:
            send(text, chat_id=chat)
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
                                  "and I'll take it from there. /stop interrupts me, /new starts a fresh conversation.")
            else:
                self._reply(chat, "This is a private assistant. Send the pairing code to connect.")
            return
        if chat != owner:
            return  # strangers are ignored
        if text in ("/start", "/help"):
            return self._reply(chat, "At your service, sir. Text, voice note or photo. /stop interrupts me, /new starts fresh.")
        if text == "/stop":
            self.runner.stop()
            return self._reply(chat, "Stopped.")
        if text == "/new":
            if self.runner.busy:
                return self._reply(chat, "I'm mid-task, sir. Send /stop first.")
            self.runner.reset()
            return self._reply(chat, "Fresh conversation. Memory is kept.")
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
        if not text and not images:
            return
        if self.runner.busy:
            return self._reply(chat, "I'm still on the last request, sir. Send /stop to interrupt it.")
        self.waiting, self.voice_reply, self.said = True, bool(voice), []
        self.hub.publish({"type": "owner", "text": text or "(photo)", "via": "telegram"})
        try:
            self.runner.start(PREFIX + text if text else PREFIX, images)
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            self.waiting = False
            self._reply(chat, f"I can't do that right now: {exc}")

    def _listen(self) -> None:
        q = self.hub.subscribe()
        try:
            while self.running:
                try:
                    ev = q.get(timeout=1)
                except Exception:
                    continue
                if not self.waiting:
                    continue
                kind, owner = ev.get("type"), settings().get("chat_id")
                if kind == "say" and ev.get("text"):
                    self.said.append(ev["text"])
                elif kind == "reply" and ev.get("text") and (ev.get("is_error") or not self.said):
                    self.said.append(ev["text"])
                elif kind == "dispatch":  # long jobs: say who's on it now, the answer comes at the end
                    self._reply(owner, f"On it: the {ev.get('agent')} agent is working on {ev.get('description') or 'it'}.")
                elif kind == "error":
                    self.said.append("Problem: " + ev.get("text", "something went wrong."))
                elif kind == "status" and ev.get("status") == "idle":
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
        finally:
            self.hub.unsubscribe(q)
