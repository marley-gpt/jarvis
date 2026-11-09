"""Stands in for the call's quick model (`claude -p --safe-mode ... --model haiku`, stream-json in and out).

Streams each reply as small partial chunks, as the real CLI does with --include-partial-messages. Anything about
sales or spend is handed off to the main session with a [[handoff: ...]] marker split across chunks; the warm-up
turn answers "ready"; everything else gets small talk. With --fake-echo-context it repeats any "(JARVIS said: ...)"
line it was given, so a test can see that context arrived; with --fake-echo-recap it repeats the first line of an
"(Earlier on this call: ...)" recap.
"""
import json
import sys


def emit(obj):
    print(json.dumps(obj), flush=True)


for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("type") != "user":
        continue
    text = " ".join(b["text"] for b in msg["message"]["content"] if b["type"] == "text")
    if "connected" in text:
        reply = "ready"
    elif "sell" in text or "spend" in text:
        reply = "Let me check that, sir. [[handoff: Tell the owner what sold today. Then give the ad spend.]]"
    elif "--fake-echo-recap" in sys.argv and "(Earlier on this call:" in text:
        reply = "Recap: " + text.split("(Earlier on this call:")[1].strip().splitlines()[0].rstrip(")")
    elif "--fake-echo-context" in sys.argv and "(JARVIS said:" in text:
        reply = "You heard: " + text.split("(JARVIS said:")[1].split(")")[0].strip()
    else:
        reply = "Very good, sir. All quiet on my side, anything else?"
    for i in range(0, len(reply), 7):
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                                 "delta": {"type": "text_delta", "text": reply[i:i + 7]}}})
    emit({"type": "stream_event", "event": {"type": "content_block_stop"}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": reply})
