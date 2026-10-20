"""Kokoro text-to-speech worker for the dashboard.

Runs in its own Python (brain/tts/venv, a version kokoro-onnx supports) so the
dashboard itself stays stdlib-only. It loads the model once, then answers one
JSON request per stdin line with one JSON line on stdout:

    -> {"text": "Good evening.", "voice": "bm_george", "speed": 1.0}
    <- {"ok": true, "wav": "<base64 WAV>", "seconds": 1.2}

The first line it prints is {"ready": true, "voices": [...]} once the model is loaded.
Standalone on purpose: it must not import the jarvis package.
"""

import argparse
import base64
import io
import json
import sys
import wave


def wav_bytes(samples, rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--voices", required=True)
    args = ap.parse_args()

    from kokoro_onnx import Kokoro

    kokoro = Kokoro(args.model, args.voices)
    out = sys.stdout
    out.write(json.dumps({"ready": True, "voices": sorted(kokoro.get_voices())}) + "\n")
    out.flush()
    for line in sys.stdin:
        try:
            req = json.loads(line)
            voice = req.get("voice") or "bm_george"
            samples, rate = kokoro.create(req["text"], voice=voice, speed=float(req.get("speed") or 1.0),
                                          lang="en-gb" if voice[:1] == "b" else "en-us")
            reply = {"ok": True, "wav": base64.b64encode(wav_bytes(samples, rate)).decode(),
                     "seconds": round(len(samples) / rate, 3)}
        except Exception as exc:  # report and keep serving
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
