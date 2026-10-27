"""Kokoro text-to-speech worker for the dashboard.

Runs in its own Python (brain/tts/venv, a version kokoro-onnx supports) so the
dashboard itself stays stdlib-only. It loads the model once, then answers one
JSON request per stdin line with one JSON line on stdout:

    -> {"text": "Good evening.", "voice": "bm_george", "speed": 1.0}
    <- {"ok": true, "wav": "<base64 WAV>", "seconds": 1.2}

A request may also carry "blend" ({"bm_george": 0.8, "bm_fable": 0.2}: a mix of
voice styles, used instead of "voice") and "fx": "jarvis" (the clean, slightly
processed sound of a house AI: low cut, presence lift, gentle compression, a short
room and a faint doubling).

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


def jarvis_fx(x, rate: int):
    """Make a dry voice sound like it comes from a house AI's speakers, without changing who it sounds like."""
    import numpy as np
    from scipy import signal

    x = signal.sosfilt(signal.butter(2, 90, "highpass", fs=rate, output="sos"), x)
    presence = signal.sosfilt(signal.butter(2, [2500, 6000], "bandpass", fs=rate, output="sos"), x)
    x = x + 0.35 * presence
    env = np.sqrt(signal.sosfilt(signal.butter(1, 12, fs=rate, output="sos"), x * x).clip(1e-9))
    x = x * np.minimum(1.0, (0.12 / env) ** 0.4)  # gentle compression above about -18 dBFS
    rng = np.random.default_rng(7)
    n = int(0.12 * rate)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.025 * rate))
    room = signal.fftconvolve(x, ir / np.abs(ir).sum() * 6)[: len(x)]
    d = int(0.009 * rate)
    double = np.concatenate([np.zeros(d), x[:-d]])
    y = x + 0.10 * room + 0.12 * double
    return (y / max(1e-6, np.abs(y).max()) * 0.89).astype("float32")


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
            style = voice
            if req.get("blend"):
                style = sum(float(w) * kokoro.get_voice_style(v) for v, w in req["blend"].items())
                voice = next(iter(req["blend"]))
            samples, rate = kokoro.create(req["text"], voice=style, speed=float(req.get("speed") or 1.0),
                                          lang="en-gb" if voice[:1] == "b" else "en-us")
            if req.get("fx") == "jarvis":
                samples = jarvis_fx(samples, rate)
            reply = {"ok": True, "wav": base64.b64encode(wav_bytes(samples, rate)).decode(),
                     "seconds": round(len(samples) / rate, 3)}
        except Exception as exc:  # report and keep serving
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
