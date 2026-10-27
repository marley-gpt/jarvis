"""JARVIS's spoken voice: Kokoro neural TTS, running locally and offline.

The model needs onnxruntime, which may not exist for the Python the dashboard
runs on, so it lives in its own environment under brain/tts/ (git-ignored):

    brain/tts/venv/      Python 3.12 venv with kokoro-onnx
    brain/tts/models/    kokoro-v1.0.onnx and voices-v1.0.bin

Set up once (free, about 350 MB):

    py -3.12 -m venv brain/tts/venv
    brain/tts/venv/Scripts/python -m pip install kokoro-onnx
    download kokoro-v1.0.onnx and voices-v1.0.bin from
    https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0 into brain/tts/models/

`Kokoro` starts tts_worker.py in that environment on first use, keeps it
running, and turns text into WAV bytes. If anything is missing the dashboard
falls back to the browser's own voice. "tts_command" in brain/config.json
overrides the worker command (tests use a fake one).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from pathlib import Path

from . import paths

# Kokoro v1.0's British voices, shown in the dashboard's voice pickers.
BRITISH_VOICES = {
    "bm_george": "George", "bm_lewis": "Lewis", "bm_daniel": "Daniel", "bm_fable": "Fable",
    "bf_emma": "Emma", "bf_isabella": "Isabella", "bf_alice": "Alice", "bf_lily": "Lily",
}
# Tuned toward the films' JARVIS without cloning anyone: George's pitch already matches his (about 140 Hz
# median), a little Fable or Daniel adds range or crispness, a touch faster, and the house-AI sound (fx).
VOICE_PRESETS = {
    "jarvis": {"name": "JARVIS", "blend": {"bm_george": 0.8, "bm_fable": 0.2}, "speed": 1.05, "fx": "jarvis"},
    "jarvis_crisp": {"name": "JARVIS (crisp)", "blend": {"bm_george": 0.7, "bm_daniel": 0.3}, "speed": 1.05, "fx": "jarvis"},
    "jarvis_dry": {"name": "JARVIS (no effect)", "blend": {"bm_george": 0.8, "bm_fable": 0.2}, "speed": 1.05},
}
DEFAULT_VOICE = "jarvis"
MAX_CHARS = 600


class TTSError(Exception):
    pass


def tts_dir() -> Path:
    return paths.home() / "tts"


def default_command() -> list[str] | None:
    base = tts_dir()
    py = base / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    model, voices = base / "models" / "kokoro-v1.0.onnx", base / "models" / "voices-v1.0.bin"
    if not (py.exists() and model.exists() and voices.exists()):
        return None
    return [str(py), str(Path(__file__).with_name("tts_worker.py")), "--model", str(model), "--voices", str(voices)]


class Kokoro:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.voices: list[str] = []
        self.error: str | None = None

    @staticmethod
    def command() -> list[str] | None:
        cfg = paths.config().get("tts_command")
        if cfg:
            return cfg if isinstance(cfg, list) else [cfg]
        return default_command()

    def info(self) -> dict:
        cfg = paths.config()
        voices = [v for v in BRITISH_VOICES if not self.voices or v in self.voices]
        presets = [{"id": k, "name": p["name"]} for k, p in VOICE_PRESETS.items()
                   if not self.voices or all(v in self.voices for v in p["blend"])]
        return {"available": bool(self.command()) and not self.error, "voice": cfg.get("tts_voice", DEFAULT_VOICE),
                "voices": presets + [{"id": v, "name": BRITISH_VOICES[v]} for v in voices], "error": self.error}

    def _start(self) -> None:
        cmd = self.command()
        if not cmd:
            raise TTSError("Kokoro is not installed (see core/jarvis/tts.py).")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", cwd=paths.REPO_ROOT)
        hello = self.proc.stdout.readline()
        try:
            ready = json.loads(hello)
        except json.JSONDecodeError:
            ready = {}
        if not ready.get("ready"):
            self.stop()
            self.error = "Kokoro failed to start."
            raise TTSError(self.error)
        self.voices = ready.get("voices") or []
        self.error = None

    def warm(self) -> None:
        """Load the model in the background so the first reply isn't slow."""
        try:
            with self.lock:
                if not (self.proc and self.proc.poll() is None):
                    self._start()
        except (TTSError, OSError):
            pass

    def speak(self, text: str, voice: str | None = None, speed: float | None = None) -> bytes:
        text = " ".join((text or "").split())[:MAX_CHARS]
        if not text:
            raise TTSError("Nothing to say.")
        cfg = paths.config()
        if voice not in BRITISH_VOICES and voice not in VOICE_PRESETS:
            voice = cfg.get("tts_voice", DEFAULT_VOICE)
        preset = VOICE_PRESETS.get(voice)
        if preset:
            req = {"text": text, "voice": next(iter(preset["blend"])), "blend": preset["blend"],
                   "speed": speed or preset["speed"], "fx": preset.get("fx")}
        else:
            req = {"text": text, "voice": voice, "speed": speed or cfg.get("tts_speed", 1.0)}
        with self.lock:
            for attempt in (1, 2):  # restart once if the worker died
                if not (self.proc and self.proc.poll() is None):
                    self._start()
                try:
                    self.proc.stdin.write(json.dumps(req) + "\n")
                    self.proc.stdin.flush()
                    line = self.proc.stdout.readline()
                    if not line:
                        raise OSError("worker exited")
                    break
                except OSError:
                    self.stop()
                    if attempt == 2:
                        raise TTSError("Kokoro stopped unexpectedly.")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise TTSError(reply.get("error") or "Kokoro could not speak that.")
        return base64.b64decode(reply["wav"])

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except OSError:
                pass
            for stream in (self.proc.stdin, self.proc.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            self.proc = None
