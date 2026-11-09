"""Chatterbox (MIT) voice-clone worker: speaks in the voice of a reference recording.

Runs in its own Python (brain/tts/clone-venv, with chatterbox-tts and CPU torch) and
answers the same JSON-lines protocol as tts_worker.py:

    -> {"text": "Good evening.", "clone": "C:/.../reference.wav", "exaggeration": 0.35, "cfg": 0.45}
    <- {"ok": true, "wav": "<base64 WAV>", "seconds": 1.2}

Only ever point it at a reference voice we are licensed to use (see
brain/files/voices/T91/LICENCE.md). Standalone on purpose: it must not import the jarvis package.
"""

import base64
import json
import sys
import warnings

from tts_worker import polish_fx, wav_bytes


def main() -> None:
    warnings.filterwarnings("ignore")
    out, sys.stdout = sys.stdout, sys.stderr  # the model prints while loading; keep stdout for replies only
    import torch
    from chatterbox.tts import ChatterboxTTS

    model = ChatterboxTTS.from_pretrained(device="cpu")
    out.write(json.dumps({"ready": True, "voices": []}) + "\n")
    out.flush()
    primed = None
    for line in sys.stdin:
        try:
            req = json.loads(line)
            exag = float(req.get("exaggeration") or 0.35)
            if primed != (req["clone"], exag):  # the reference's voice features are cached between requests
                model.prepare_conditionals(req["clone"], exaggeration=exag)
                primed = (req["clone"], exag)
            torch.manual_seed(int(req.get("seed") or 7))
            wav = model.generate(req["text"], exaggeration=exag, cfg_weight=float(req.get("cfg") or 0.45))
            samples = wav.squeeze(0).numpy()
            if req.get("fx") == "polish":
                samples = polish_fx(samples, model.sr)
            reply = {"ok": True, "wav": base64.b64encode(wav_bytes(samples, model.sr)).decode(),
                     "seconds": round(len(samples) / model.sr, 3)}
        except Exception as exc:  # report and keep serving
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
