"""Stand-in for tts_worker.py: same line protocol, returns a short silent WAV."""
import base64
import io
import json
import sys
import wave

print(json.dumps({"ready": True, "voices": ["bf_emma", "bm_daniel", "bm_george", "bm_lewis"]}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if "boom" in req["text"]:
        print(json.dumps({"ok": False, "error": "could not speak"}), flush=True)
        continue
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 2400)
    print(json.dumps({"ok": True, "wav": base64.b64encode(buf.getvalue()).decode(), "seconds": 0.1,
                      "echo": req}), flush=True)
