"""Stands in for stt_worker.py: every bit of speech is heard as the same sentence."""
import json
import sys

print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    ok = json.loads(line).get("wav")
    print(json.dumps({"ok": True, "text": "what did we sell today"} if ok else {"ok": False, "error": "no audio"}),
          flush=True)
