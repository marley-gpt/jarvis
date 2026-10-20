"""Stand-in for wake_worker.py: reports ready, hears "Hey JARVIS" once, then waits for quit."""
import sys

print("READY", flush=True)
print("WAKE 0.93", flush=True)
for line in sys.stdin:
    if line.strip() == "quit":
        break
