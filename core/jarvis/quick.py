"""The call's quick brain: a small, fast model that holds the spoken back-and-forth of a phone call.

The main JARVIS session (the dashboard's Claude Code, full memory, hooks and tools) takes 2.5 to 4.5 s to its
first sentence even when warm, and can be busy with other work for minutes. That is too slow to talk to. So a
call answers with Haiku through Claude Code itself (the owner's subscription, no API bill), started in safe mode
with no tools, no hooks and no CLAUDE.md, and only a short brief in its system prompt: its first words come in
about a second. Anything that needs real data, tools or work it hands to the main session with
[[handoff: what to do]], says so out loud, and the main session's answer is spoken on the call when it's ready.

    "call_quick_model"    in brain/config.json: the model (default haiku); "off" sends every turn to the main session
    "call_quick_command"  overrides the command (tests use a fake one)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time

from . import paths

PROMPT = """You are JARVIS, the owner's AI chief of staff for Urban Supplied (a Shopify store testing products with \
Meta static ads), on a live phone call with the owner. Speak as JARVIS from the films: a calm, courteous British butler \
and engineer with a dry, light wit. Call the owner "sir" now and then, not every line.

This is a phone call, so talk the way a person does on the phone, not like a chatbot:
- HARD LIMIT: two short sentences, under 30 words in all, then stop and let the owner talk. Never a monologue; \
give the one thing that matters and ask if they want more. Don't volunteer business news they didn't ask about.
- React to what they just said first (their words, their mood), then answer. Pick up on earlier things from this call \
when they matter ("the brush you mentioned").
- Most turns end with a short, natural follow-up question, the kind a person would ask next. Not every turn: if \
they're wrapping up, let them go.
- Contractions and plain everyday words. A light "right", "ah" or "well" now and then is fine; a short "mm" may \
already have played, so don't stack fillers, and never open with "Certainly", "Absolutely" or "Great question".
- Never read out ids, task or memory numbers, file paths, links or code. Say "the brush test", not "T84".
- No lists, markdown, emoji or stage directions. Never say "as an AI" or talk about being a model; you're JARVIS.
- If they talk over you or change the subject, drop what you were saying and follow them.

How turns should sound (the length and feel, not the words):
Owner: hey jarvis, how are we going
JARVIS: Ticking along nicely, sir. How's your day been?
Owner: long, been at work all day
JARVIS: Ah, one of those. Want the quick version of today, or shall I leave you be?
Owner: what was I meant to do about the brush
JARVIS: Mainly the two new brush ads, they need your eye before they go live. Shall I send them over?

You are the quick voice of JARVIS on this call. The full JARVIS (memory, the store, the ad account, the team of \
agents, the PC) runs alongside you. You only know what is in the brief below and what was said on this call. \
Never invent numbers, sales, results, names or facts. When the owner asks for anything you can't answer from the brief or \
the call, or asks you to do, check, start, change, send or look up anything, say one short line such as "Let me \
check that, sir." and end your reply with exactly one marker: [[handoff: <the request, in full, as an instruction \
to the full JARVIS>]]. The full JARVIS will speak the answer on this call itself; don't promise a time. Never put a \
handoff in for small talk, a greeting, or something you just answered. Lines that start "(JARVIS said:" are what \
the full JARVIS has already said on the call: don't repeat them.

The brief (today's state of the business):
"""
HANDOFF = re.compile(r"\[\[\s*handoff\s*:?\s*(.*?)\s*\]\]", re.I | re.S)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_END = re.compile(r"(?<=[,;:])\s+|\s+(?:-|–|—)\s+")
FIRST_CLAUSE = 24  # characters before a reply's opening clause may go to the voice on its own
WARM_UP = "(The call has just connected. Reply with only the word: ready)"


def model() -> str:
    return str(paths.config().get("call_quick_model") or "haiku")


def command() -> list[str] | None:
    cfg = paths.config().get("call_quick_command")
    if cfg:
        return cfg if isinstance(cfg, list) else [cfg]
    if model() == "off":
        return None
    from .dashboard import find_claude
    claude = find_claude()
    if not claude:
        return None
    return [*claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--safe-mode", "--tools", "", "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence", "--model", model()]


def brief_text() -> str:
    """The short state of the business the quick brain may talk about (the same brief a session starts with)."""
    try:
        from . import brief, db
        conn = db.connect()
        try:
            return brief.session_brief(conn)[:6000]
        finally:
            conn.close()
    except Exception as exc:  # a call must still connect without its brief
        return f"(The brief couldn't be loaded: {exc}. Hand off anything about the business.)"


class QuickBrain:
    """One long-lived quick-model process per call server: kept warm, so a reply never waits for a start-up."""

    def __init__(self, cmd: list[str] | None = None):
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.started = 0.0
        self.fresh = True

    def available(self) -> bool:
        return bool(self.cmd or command())

    def _spawn(self) -> None:
        cmd = self.cmd or command()
        if not cmd:
            raise RuntimeError("No quick model for calls (Claude Code not found, or call_quick_model is off).")
        stamp = time.strftime("%A %d %B %Y, %H:%M")
        cmd = [*cmd, "--system-prompt", PROMPT + brief_text() + f"\n\nIt is {stamp} for the owner."]
        # A folder outside the repo, so nothing from the project loads even if safe mode changes.
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", cwd=tempfile.gettempdir(),
                                     creationflags=paths.NO_WINDOW if os.name == "nt" else 0)
        self.started = time.time()
        self.fresh = True  # no conversation in this process yet

    def _alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def warm(self) -> None:
        """Start the process and spend one throwaway turn on it, so the owner's first line is answered warm."""
        with self.lock:
            if self._alive():
                return
            self._spawn()
            self._turn(WARM_UP, lambda s: None)

    def new_call(self) -> None:
        """A fresh conversation (and a fresh brief) for each call."""
        self.stop()

    def reply(self, text: str, on_sentence, on_first=None, recap: str = "") -> str:
        """Answer one turn, handing each spoken sentence to on_sentence as soon as it's written.
        `recap` is the call so far: it's only sent if the process had to start again, so it never loses the thread.
        Returns the handoff instruction for the main JARVIS, or ''."""
        with self.lock:
            if not self._alive():
                self._spawn()
            if self.fresh and recap:
                text = f"(Earlier on this call:\n{recap})\n{text}"
            self.fresh = False
            return self._turn(text, on_sentence, on_first)

    def _turn(self, text: str, on_sentence, on_first=None) -> str:
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self.stop()
            raise RuntimeError("The quick model stopped.")
        buf, full, spoken = "", "", False

        def speak(piece: str) -> None:
            nonlocal spoken
            piece = HANDOFF.sub("", piece).strip()
            if piece and "[[" not in piece:
                if not spoken and on_first:
                    on_first()
                spoken = True
                on_sentence(piece)

        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "stream_event" and not ev.get("parent_tool_use_id"):
                e = ev.get("event") or {}
                if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                    piece = e["delta"].get("text", "")
                    buf += piece
                    full += piece
                    if buf.rfind("[[") > buf.rfind("]]"):
                        continue  # a handoff marker is still arriving: never speak it
                    buf = HANDOFF.sub("", buf)  # whole, before splitting: a marker with a full stop in it stays unsaid
                    *done, buf = SENTENCE_END.split(buf)
                    for sentence in done:
                        speak(sentence)
                    if not spoken and len(buf) > FIRST_CLAUSE:  # a long first sentence: its opening clause goes now
                        *lead, buf = CLAUSE_END.split(buf)
                        for clause in lead:
                            speak(clause)
            elif kind == "result":
                if not full and ev.get("result"):  # no partial messages: the whole reply at once
                    full, buf = ev["result"], HANDOFF.sub("", ev["result"])
                    *done, buf = SENTENCE_END.split(buf)
                    for sentence in done:
                        speak(sentence)
                speak(buf)
                found = HANDOFF.search(full)
                return found.group(1).strip() if found else ""
        self.stop()
        raise RuntimeError("The quick model stopped mid-reply.")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
