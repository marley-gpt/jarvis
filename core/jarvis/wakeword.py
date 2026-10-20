"""'Hey JARVIS': runs wake_worker.py in the brain/tts venv and tells the dashboard when it hears it.

Only the wake word is listened for, locally (openWakeWord's hey_jarvis model); nothing
is recorded or transcribed until the owner says "Hey JARVIS". Then the dashboard page
greets them and starts normal speech recognition for the request.

On by default when the venv has openwakeword; `"wake_word": false` in brain/config.json
turns it off, `"wake_threshold"` tunes sensitivity (0-1, default 0.5), and
`"wake_command"` overrides the worker command (tests use a fake one).
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from . import paths, tts


class WakeWord:
    def __init__(self, on_wake):
        self.on_wake = on_wake
        self.proc: subprocess.Popen | None = None
        self.ready = False
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
        return [str(py), str(Path(__file__).with_name("wake_worker.py")), str(cfg.get("wake_threshold", 0.5))]

    def enabled(self) -> bool:
        return paths.config().get("wake_word", True) is not False

    def info(self) -> dict:
        return {"available": self.command() is not None, "on": bool(self.proc and self.proc.poll() is None),
                "ready": self.ready, "error": self.error}

    def start(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            cmd = self.command()
            if not cmd:
                self.error = "Wake word engine not installed (brain/tts/venv with openwakeword)."
                return
            self.ready, self.error = False, None
            flags = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0  # no console window on Windows
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", creationflags=flags)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.strip()
            if line == "READY":
                self.ready = True
            elif line.startswith("WAKE"):
                self.on_wake(line)
            elif line.startswith("ERROR"):
                self.error = line[6:]
        self.ready = False

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
