"""Speech-to-text worker for JARVIS calls: local faster-whisper, nothing leaves the PC.

Runs in the brain/tts venv (where faster-whisper lives) so the call server stays stdlib-only.
It loads the model once, prints {"ready": true}, then answers one JSON line per request:

    -> {"wav": "<base64 16 kHz mono 16-bit WAV>"}
    <- {"ok": true, "text": "what did we sell today"}

Standalone on purpose: it must not import the jarvis package.
"""

import base64
import io
import json
import sys
import wave


def pcm(wav_b64: str):
    import numpy as np

    with wave.open(io.BytesIO(base64.b64decode(wav_b64))) as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


def main() -> None:
    from faster_whisper import WhisperModel

    import numpy as np

    # base.en: about 0.85 s for a 5 s line on the i5-14400 CPU, against 2.9 s for small.en (T100). The phone's
    # page already cut the speech out, so Whisper's own VAD pass and timestamps only cost time.
    model = WhisperModel(sys.argv[1] if len(sys.argv) > 1 else "base.en", device="cpu", compute_type="int8")
    model.transcribe(np.zeros(16000, dtype="float32"), language="en", beam_size=1)  # first pass is slow: do it now
    out = sys.stdout
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        try:
            segments, _ = model.transcribe(pcm(json.loads(line)["wav"]), language="en", beam_size=1,
                                           condition_on_previous_text=False, without_timestamps=True)
            reply = {"ok": True, "text": " ".join(s.text.strip() for s in segments).strip()}
        except Exception as exc:  # report and keep serving
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
