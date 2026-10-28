"""'Hey JARVIS': runs wake_worker.py in the brain/tts venv, JARVIS's ears.

Only the wake word is listened for, locally (openWakeWord's hey_jarvis model). When the owner
says "Hey JARVIS" the worker records the request until they stop talking and transcribes it
on this PC (faster-whisper), and the dashboard passes it to JARVIS. `listen()` does the same
without the wake word (the mic button, and follow-ups in a conversation). Without
faster-whisper the page falls back to the browser's speech recognition.

On by default when the venv has openwakeword; `"wake_word": false` in brain/config.json
turns it off, `"wake_threshold"` tunes sensitivity (0-1, default 0.5), and
`"stt_model"` picks the Whisper model (default small.en), and `"wake_command"` overrides the
worker command (tests use a fake one).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from . import paths, tts


class WakeWord:
    def __init__(self, on_wake, on_heard=None, on_state=None):
        self.on_wake, self.on_heard, self.on_state = on_wake, on_heard, on_state
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self.stt = False  # the worker can transcribe (faster-whisper loaded)
        self.error: str | None = None
        self.lock = threading.Lock()

    def command(self) -> list[str] | None:
        cfg = paths.config()
        if cfg.get("wake_command"):
            return list(cfg["wake_command"])
        py = tts.tts_dir() / "venv" / "Scripts" / "python.exe"
        if not py.exists():
            py = tts.tts_dir() / "venv" / "bin" / "python"
        if not py.exists():
            return None
        return [str(py), str(Path(__file__).with_name("wake_worker.py")), str(cfg.get("wake_threshold", 0.5)),
                cfg.get("stt_model", "small.en")]

    def enabled(self) -> bool:
        return paths.config().get("wake_word", True) is not False

    def info(self) -> dict:
        return {"available": self.command() is not None, "on": bool(self.proc and self.proc.poll() is None),
                "ready": self.ready, "stt": self.ready and self.stt, "error": self.error}

    def start(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            cmd = self.command()
            if not cmd:
                self.error = "Wake word engine not installed (brain/tts/venv with openwakeword)."
                return
            self.ready, self.error = False, None
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", creationflags=paths.NO_WINDOW)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("READY"):
                self.ready, self.stt = True, line.endswith(" stt")
            elif line.startswith("WAKE"):
                self.on_wake(line)
            elif line in ("LISTENING", "THINKING", "NOSPEECH"):
                if self.on_state:
                    self.on_state(line.lower())
            elif line.startswith("HEARD "):
                if self.on_state:
                    self.on_state("heard")
                if self.on_heard:
                    self.on_heard(json.loads(line[6:]).get("text", ""))
            elif line.startswith("ERROR"):
                self.error = line[6:]
        self.ready = False

    def listen(self, followup: bool = False) -> bool:
        """Record and transcribe one request now, without the wake word. False if the worker can't."""
        if not (self.ready and self.stt):
            return False
        self.send("followup" if followup else "listen")
        return True

    def send(self, cmd: str) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
        except OSError:
            pass

    def stop(self) -> None:
        self.send("quit")
        proc, self.proc = self.proc, None
        if proc:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.ready = False
