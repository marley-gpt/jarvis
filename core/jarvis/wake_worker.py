"""Listen on the microphone for "Hey JARVIS" and nothing else (runs in the brain/tts venv).

openWakeWord's pre-trained "hey_jarvis" model scores each 80 ms of audio locally. Audio
is never stored, transcribed or sent anywhere. The worker only prints one line per
detection to stdout:

    READY                 model loaded and microphone open
    WAKE 0.87             "Hey JARVIS" heard (score)

stdin accepts "pause" / "resume" (ignore audio while JARVIS himself is talking) and "quit".
"""

from __future__ import annotations

import sys
import threading
import time


def main() -> int:
    threshold = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model

    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    state = {"paused": False, "quit": False, "last": 0.0}

    def commands():
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd == "pause":
                state["paused"] = True
            elif cmd == "resume":
                state["paused"] = False
                model.reset()
            elif cmd == "quit":
                state["quit"] = True
                return
        state["quit"] = True  # parent went away

    threading.Thread(target=commands, daemon=True).start()
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1280) as stream:
        print("READY", flush=True)
        while not state["quit"]:
            audio, _ = stream.read(1280)
            if state["paused"]:
                continue
            scores = model.predict(np.frombuffer(audio, dtype=np.int16))
            score = max(scores.values()) if scores else 0.0
            now = time.time()
            if score >= threshold and now - state["last"] > 3:
                state["last"] = now
                print(f"WAKE {score:.2f}", flush=True)
                model.reset()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # report and exit; the dashboard shows the error
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
