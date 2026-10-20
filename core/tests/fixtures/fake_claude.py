"""Stand-in for `claude -p --output-format stream-json`, emitting the event shapes the dashboard parses."""
import json
import sys

prompt = sys.stdin.read()
resumed = "--resume" in sys.argv
sid = sys.argv[sys.argv.index("--resume") + 1] if resumed else "sess-123"


def emit(obj):
    print(json.dumps(obj), flush=True)


if "--fake-missing-session" in sys.argv and resumed:
    print(f"No conversation found with session ID: {sid}", file=sys.stderr)
    sys.exit(1)

emit({"type": "system", "subtype": "init", "session_id": sid})
if "--fake-logged-out" in sys.argv:
    emit({"type": "assistant", "parent_tool_use_id": None,
          "message": {"content": [{"type": "text", "text": "Not logged in · Please run /login"}]}})
    emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login",
          "session_id": sid})
    sys.exit(1)
emit({"type": "assistant", "parent_tool_use_id": None,
      "message": {"content": [{"type": "text", "text": f"On it. Resumed={resumed}. You said: {prompt}"}]}})
emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
    {"type": "tool_use", "id": "tu1", "name": "Agent",
     "input": {"subagent_type": "product-research", "description": "Research posture corrector", "prompt": "..."}}]}})
emit({"type": "assistant", "parent_tool_use_id": "tu1", "message": {"content": [
    {"type": "tool_use", "id": "tu2", "name": "WebSearch", "input": {"query": "posture corrector reviews"}}]}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "done"}]}})
emit({"type": "result", "subtype": "success", "result": "Research is done.", "session_id": sid, "total_cost_usd": 0})
