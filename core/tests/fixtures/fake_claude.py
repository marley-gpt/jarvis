"""Stand-in for `claude -p --input-format stream-json --output-format stream-json`.

Reads one JSON user message per stdin line and answers each with the event shapes the
dashboard parses, staying alive between messages like the real CLI. Flags:
--fake-logged-out (answers with the sign-in error, then exits), --fake-missing-session
(exits at once when resuming), --fake-stream (streams the first reply as partial chunks).
"""
import json
import sys

resumed = "--resume" in sys.argv
sid = sys.argv[sys.argv.index("--resume") + 1] if resumed else "sess-123"


def emit(obj):
    print(json.dumps(obj), flush=True)


if "--fake-missing-session" in sys.argv and resumed:
    print(f"No conversation found with session ID: {sid}", file=sys.stderr)
    sys.exit(1)

turn = 0
for line in sys.stdin:
    msg = json.loads(line)["message"]
    texts = [b["text"] for b in msg["content"] if b["type"] == "text"]
    images = sum(1 for b in msg["content"] if b["type"] == "image")
    turn += 1
    emit({"type": "system", "subtype": "init", "session_id": sid})
    if "--fake-logged-out" in sys.argv:
        emit({"type": "assistant", "parent_tool_use_id": None,
              "message": {"content": [{"type": "text", "text": "Not logged in · Please run /login"}]}})
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login",
              "session_id": sid})
        sys.exit(1)
    reply = f"On it. Resumed={resumed}. Turn={turn}. Images={images}. You said: {' '.join(texts)}"
    if "--fake-stream" in sys.argv:
        for piece in ("On it, sir. The research team ", "is starting now. Shall I ", "brief the creative team too?"):
            emit({"type": "stream_event", "parent_tool_use_id": None,
                  "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}}})
        emit({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "content_block_stop", "index": 0}})
    emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": reply}]}})
    emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": "tu1", "name": "Agent",
         "input": {"subagent_type": "product-research", "description": "Research posture corrector", "prompt": "..."}}]}})
    emit({"type": "assistant", "parent_tool_use_id": "tu1", "message": {"content": [
        {"type": "tool_use", "id": "tu2", "name": "WebSearch", "input": {"query": "posture corrector reviews"}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "done"}]}})
    emit({"type": "result", "subtype": "success", "result": "Research is done.", "session_id": sid, "total_cost_usd": 0})
