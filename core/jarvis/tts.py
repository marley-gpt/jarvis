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
import re
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
# median) and a little Fable adds range. The owner picked the clean version (sample A, 2026-10-29): the
# old "house AI" effect (echo and doubling) made it sound robotic, so it's kept only as an option.
VOICE_PRESETS = {
    "jarvis": {"name": "JARVIS", "blend": {"bm_george": 0.8, "bm_fable": 0.2}, "speed": 1.0},
    "jarvis_warm": {"name": "JARVIS (warmer)", "blend": {"bm_george": 0.6, "bm_lewis": 0.4}, "speed": 0.97},
    "jarvis_crisp": {"name": "JARVIS (crisp)", "blend": {"bm_daniel": 0.6, "bm_george": 0.4}, "speed": 1.0},
    # T87 sample B, the owner's pick (2026-11-09): drier and crisper. The sample had a -0.5 semitone shift,
    # which the worker doesn't do; at that size it's barely audible.
    "jarvis_b": {"name": "JARVIS (dry and crisp)", "blend": {"bm_daniel": 0.5, "bm_george": 0.5}, "speed": 1.0,
                 "fx": "polish"},
    "jarvis_fx": {"name": "JARVIS (house-AI effect)", "blend": {"bm_george": 0.8, "bm_fable": 0.2}, "speed": 1.05,
                  "fx": "jarvis"},
    # T91: Chatterbox (local, MIT) cloning an ORIGINAL reference voice designed from a text description with
    # Parler-TTS (Apache-2.0); no actor, reel or real person. Licence: brain/files/voices/T91/LICENCE.md.
    "jarvis-clone": {"name": "JARVIS (cloned butler)", "clone": "files/voices/T91/reference.wav", "speed": 1.0,
                     "exaggeration": 0.35, "cfg": 0.45, "fx": "polish"},
}
# Each head agent, and the Blake and Mark method personas, speaks with its own voice so the owner can hold a
# conversation with any of them. Stock Kokoro British voices and blends of them, clearly different from each
# other; nothing here clones or imitates a real person's voice.
AGENT_VOICES = {
    "jarvis": "jarvis",
    "product-research": {"name": "Product research", "blend": {"bm_lewis": 1.0}, "speed": 1.0},
    "market-research": {"name": "Market research", "blend": {"bf_emma": 1.0}, "speed": 1.0},
    "creative-research": {"name": "Creative research", "blend": {"bf_lily": 1.0}, "speed": 1.03},
    "mentor-learning": {"name": "Mentor learning", "blend": {"bm_daniel": 1.0}, "speed": 0.97},
    "operations": {"name": "Operations", "blend": {"bf_isabella": 1.0}, "speed": 1.0},
    "accountant": {"name": "Accountant", "blend": {"bm_daniel": 0.6, "bm_lewis": 0.4}, "speed": 0.95},
    "meta-ads": {"name": "Meta ads", "blend": {"bm_fable": 1.0}, "speed": 1.02},
    "meta-ads-auditor": {"name": "Meta ads auditor", "blend": {"bm_lewis": 0.5, "bm_daniel": 0.5}, "speed": 0.98},
    "shopify": {"name": "Shopify", "blend": {"bf_alice": 1.0}, "speed": 1.0},
    "store-qa": {"name": "Store QA", "blend": {"bf_emma": 0.5, "bf_alice": 0.5}, "speed": 1.04},
    "creative-director": {"name": "Creative director", "blend": {"bf_isabella": 0.6, "bf_lily": 0.4}, "speed": 0.98},
    "product-testing": {"name": "Product testing", "blend": {"bm_george": 0.5, "bm_daniel": 0.5}, "speed": 1.03},
    "blake": {"name": "Blake (method persona)", "blend": {"bm_lewis": 0.7, "bm_george": 0.3}, "speed": 1.05},
    "mark": {"name": "Mark (method persona)", "blend": {"bm_daniel": 0.7, "bm_fable": 0.3}, "speed": 1.0},
}
for _who, _p in AGENT_VOICES.items():
    if isinstance(_p, dict):
        VOICE_PRESETS[f"voice_{_who}"] = {**_p, "agent": _who}
DEFAULT_VOICE = "jarvis"


def agent_voice(who: str) -> str:
    """The voice id an agent (or Blake / Mark) speaks with: the owner's choice in brain/config.json, else the stock one."""
    chosen = (paths.config().get("agent_voices") or {}).get(who)
    if chosen in BRITISH_VOICES or chosen in VOICE_PRESETS:
        return chosen
    stock = AGENT_VOICES.get(who)
    return stock if isinstance(stock, str) else f"voice_{who}" if stock else DEFAULT_VOICE


def agent_voices() -> dict:
    return {who: agent_voice(who) for who in AGENT_VOICES}


def set_agent_voice(who: str, voice: str) -> str:
    """Save the owner's voice choice for an agent (or Blake / Mark); "" goes back to the stock voice."""
    if who not in AGENT_VOICES:
        raise ValueError(f"No voice slot for '{who}'.")
    if voice and voice not in BRITISH_VOICES and voice not in VOICE_PRESETS:
        raise ValueError("Pick one of the Kokoro voices or presets.")
    chosen = dict(paths.config().get("agent_voices") or {})
    if voice:
        chosen[who] = voice
    else:
        chosen.pop(who, None)
    paths.set_config("agent_voices", chosen)
    return agent_voice(who)
MAX_CHARS = 600


_LIVE_AFTER = re.compile(r"\b(go|goes|going|went|gone|is|are|was|were|be|been|it's|now|stays?|still|currently) live\b", re.I)
_LIVE_BEFORE = re.compile(r"\b(live)(?= (?:now|on|store|stores|ads?|data|feed|tile|call|calls|campaigns?|tests?|figures?"
                          r"|numbers?|voice|site|page|chat|version|server|line|sales|stream)\b)", re.I)


def say_live(text: str) -> str:
    """Kokoro reads "live" as the verb ("liv"). Where it means on air ("the store is live", "live sales"),
    spell it the way it sounds (owner, 2026-11-09)."""
    text = _LIVE_AFTER.sub(lambda m: m.group(1) + " lyve", text)
    return _LIVE_BEFORE.sub(lambda m: "Lyve" if m.group(1)[0].isupper() else "lyve", text)


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


def clone_command() -> list[str] | None:
    """The Chatterbox clone worker in brain/tts/clone-venv, if installed."""
    py = tts_dir() / "clone-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return [str(py), str(Path(__file__).with_name("clone_worker.py"))] if py.exists() else None


def clone_ref(preset: dict) -> Path | None:
    ref = paths.home() / preset["clone"] if preset.get("clone") else None
    return ref if ref and ref.exists() else None


class Kokoro:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.voices: list[str] = []
        self.error: str | None = None
        self.cloner: Cloner | None = None

    @staticmethod
    def command() -> list[str] | None:
        cfg = paths.config().get("tts_command")
        if cfg:
            return cfg if isinstance(cfg, list) else [cfg]
        return default_command()

    def usable(self, preset: dict) -> bool:
        if preset.get("clone"):
            return bool(clone_ref(preset) and Cloner.command())
        return not self.voices or all(v in self.voices for v in preset["blend"])

    def info(self) -> dict:
        cfg = paths.config()
        voices = [v for v in BRITISH_VOICES if not self.voices or v in self.voices]
        usable = self.usable
        presets = [{"id": k, "name": p["name"]} for k, p in VOICE_PRESETS.items() if not p.get("agent") and usable(p)]
        team = [{"id": k, "name": p["name"], "agent": p["agent"]} for k, p in VOICE_PRESETS.items() if p.get("agent") and usable(p)]
        return {"available": bool(self.command()) and not self.error, "voice": cfg.get("tts_voice", DEFAULT_VOICE),
                "voices": presets + [{"id": v, "name": BRITISH_VOICES[v]} for v in voices], "team_voices": team,
                "agent_voices": agent_voices(), "error": self.error}

    def _start(self) -> None:
        cmd = self.command()
        if not cmd:
            raise TTSError("Kokoro is not installed (see core/jarvis/tts.py).")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", cwd=paths.REPO_ROOT, creationflags=paths.NO_WINDOW)
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
        text = say_live(" ".join((text or "").split())[:MAX_CHARS])
        if not text:
            raise TTSError("Nothing to say.")
        cfg = paths.config()
        if voice not in BRITISH_VOICES and voice not in VOICE_PRESETS:
            voice = cfg.get("tts_voice", DEFAULT_VOICE)
        preset = VOICE_PRESETS.get(voice)
        if preset and preset.get("clone"):
            ref = clone_ref(preset)
            if not (ref and Cloner.command()):  # clone not installed here: fall back to the stock JARVIS voice
                return self.speak(text, DEFAULT_VOICE, speed)
            self.cloner = self.cloner or Cloner()
            return self.cloner.ask({"text": text, "clone": str(ref), "exaggeration": preset.get("exaggeration"),
                                    "cfg": preset.get("cfg"), "fx": preset.get("fx")})
        if preset:
            req = {"text": text, "voice": next(iter(preset["blend"])), "blend": preset["blend"],
                   "speed": speed or preset["speed"], "fx": preset.get("fx")}
        else:
            req = {"text": text, "voice": voice, "speed": speed or cfg.get("tts_speed", 1.0)}
        return self.ask(req)

    def ask(self, req: dict) -> bytes:
        """Send one request to the worker (starting it if needed) and return the WAV."""
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
        if self.cloner:
            self.cloner.stop()
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


class Cloner(Kokoro):
    """The Chatterbox clone worker (clone_worker.py), for presets with a "clone" reference voice."""

    @staticmethod
    def command() -> list[str] | None:
        return clone_command()
