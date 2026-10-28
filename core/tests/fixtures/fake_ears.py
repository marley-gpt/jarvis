"""Stand-in for wake_worker.py with speech-to-text: hears "Hey JARVIS, how are the ads going", then
answers each "listen" with a follow-up request, until quit."""
import json
import sys

print("READY stt", flush=True)
print("WAKE 0.93", flush=True)
print("LISTENING", flush=True)
print("THINKING", flush=True)
print("HEARD " + json.dumps({"text": "how are the ads going"}), flush=True)
for line in sys.stdin:
    cmd = line.strip()
    if cmd in ("listen", "followup"):
        print("LISTENING", flush=True)
        print("HEARD " + json.dumps({"text": "and the store"}), flush=True)
    elif cmd == "quit":
        break
