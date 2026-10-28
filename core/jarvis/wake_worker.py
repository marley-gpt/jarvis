"""JARVIS's ears (runs in the brain/tts venv): "Hey JARVIS", then what the owner says, all on this PC.

openWakeWord's pre-trained "hey_jarvis" model scores each 80 ms of audio. Nothing is kept or
transcribed until it fires. Then the worker records the request until the owner stops talking and
transcribes it with faster-whisper (local, free; the same model `bin/jarvis watch` uses). Audio is
never saved or sent anywhere. One line per event on stdout:

    READY stt|nostt       model loaded and microphone open (stt: speech-to-text available)
    WAKE 0.87             "Hey JARVIS" heard (score)
    LISTENING             recording the request
    THINKING              transcribing it
    HEARD {"text": ...}   what was said (JSON)
    NOSPEECH              nothing said before the wait ran out

stdin commands: "listen" (record a request now, no wake word: the mic button or a follow-up),
"followup" (the same, with a shorter wait), "cancel", "pause" / "resume" (while JARVIS speaks the
wake word needs a much higher score, so the owner can still interrupt but his own voice can't
trigger it), and "quit".
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections import deque

RATE, BLOCK = 16000, 1280  # 80 ms blocks, what openWakeWord expects
WAKE_AFTER = re.compile(r"^\W*(hey|hi|okay|ok)?\W*jarvis\W*", re.I)


def main() -> int:
    threshold = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    stt_model = sys.argv[2] if len(sys.argv) > 2 else "small.en"
    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model

    wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    try:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel
        whisper = WhisperModel(stt_model, device="cpu", compute_type="int8")
    except Exception as exc:  # wake word still works; the page falls back to the browser's recogniser
        whisper = None
        print(f"ERROR speech-to-text unavailable: {type(exc).__name__}: {exc}", flush=True)

    state = {"paused": False, "quit": False, "last": 0.0, "request": None, "cancel": False}
    floor = deque(maxlen=40)  # recent background loudness (about 3 s), for knowing when speech starts and stops

    def commands():
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd == "pause":
                state["paused"] = True
            elif cmd == "resume":
                state["paused"] = False
                wake.reset()
            elif cmd in ("listen", "followup"):
                state["request"] = cmd
            elif cmd == "cancel":
                state["cancel"] = True
            elif cmd == "quit":
                state["quit"] = True
                return
        state["quit"] = True  # parent went away

    def level(audio) -> float:
        return float(np.sqrt(np.mean(np.square(audio.astype(np.float32))))) if len(audio) else 0.0

    def record(stream, wait: float) -> "np.ndarray | None":
        """Record until the owner stops talking. None if they never started within `wait` seconds."""
        noise = sorted(floor)[len(floor) // 2] if floor else 150.0
        start_at, stop_at = max(noise * 3.0, 350.0), max(noise * 2.0, 250.0)
        print("LISTENING", flush=True)
        state["cancel"] = False
        chunks, started, quiet, t0 = [], False, 0.0, time.time()
        while not state["quit"] and not state["cancel"]:
            audio, _ = stream.read(BLOCK)
            audio = audio[:, 0] if audio.ndim > 1 else audio
            chunks.append(audio.copy())
            loud = level(audio)
            if not started:
                if loud >= start_at:
                    started, quiet = True, 0.0
                elif time.time() - t0 > wait:
                    return None
                else:
                    chunks = chunks[-5:]  # keep 0.4 s before speech so the first word isn't clipped
                continue
            quiet = quiet + BLOCK / RATE if loud < stop_at else 0.0
            if quiet >= 0.9 or len(chunks) * BLOCK / RATE > 30:
                break
        if state["cancel"] or not started:
            return None
        return np.concatenate(chunks).astype(np.float32) / 32768.0

    def transcribe(audio) -> str:
        print("THINKING", flush=True)
        segments, _ = whisper.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                         condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        return WAKE_AFTER.sub("", text).strip()

    def hear(stream, wait: float) -> None:
        audio = record(stream, wait)
        text = transcribe(audio) if audio is not None and whisper else ""
        print(f"HEARD {json.dumps({'text': text})}" if text else "NOSPEECH", flush=True)
        wake.reset()

    threading.Thread(target=commands, daemon=True).start()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
        print("READY " + ("stt" if whisper else "nostt"), flush=True)
        while not state["quit"]:
            if state["request"] and whisper:
                wait = 6.0 if state["request"] == "followup" else 8.0
                state["request"] = None
                hear(stream, wait)
                continue
            state["request"] = None
            audio, _ = stream.read(BLOCK)
            audio = audio[:, 0] if audio.ndim > 1 else audio
            floor.append(level(audio))
            scores = wake.predict(audio)
            score = max(scores.values()) if scores else 0.0
            need = max(threshold, 0.85) if state["paused"] else threshold  # JARVIS is talking: only a clear call
            now = time.time()
            if score >= need and now - state["last"] > 2:
                state["last"] = now
                print(f"WAKE {score:.2f}", flush=True)
                if whisper:
                    hear(stream, 5.0)  # "Hey JARVIS, check the store" or a pause and then the request
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # report and exit; the dashboard shows the error
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
