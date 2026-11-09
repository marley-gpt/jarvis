"""Stand-in for `claude -p --input-format stream-json --output-format stream-json`.

Reads one JSON user message per stdin line and answers each with the event shapes the
dashboard parses, staying alive between messages like the real CLI. Flags:
--fake-logged-out (answers with the sign-in error, then exits), --fake-missing-session
(exits at once when resuming), --fake-stream (streams the first reply as partial chunks),
--fake-freeze (the first process goes silent on the first message, as the real CLI once did;
the --fake-marker file is how a retry knows it isn't the first),
--fake-lost-session (resuming ends the turn with an error and no text, as the real CLI does),
--fake-deaf (the first process never reads stdin; --fake-marker as above), --fake-chatty (after a reply it writes more than
the pipe holds, and reads nothing more until that output has been read).
A message containing PERMIT asks for permission to use a tool (a `can_use_tool` control request, as with
`--permission-prompt-tool stdio`) and reports the answer; one containing STEP runs a top-level tool with a result.
"""
import json
import os
import sys
import time

resumed = "--resume" in sys.argv
sid = sys.argv[sys.argv.index("--resume") + 1] if resumed else "sess-123"


def emit(obj):
    print(json.dumps(obj), flush=True)


def first_process():
    marker = sys.argv[sys.argv.index("--fake-marker") + 1]
    if os.path.exists(marker):
        return False
    open(marker, "w").close()
    return True


if "--fake-missing-session" in sys.argv and resumed:
    print(f"No conversation found with session ID: {sid}", file=sys.stderr)
    sys.exit(1)

if "--fake-deaf" in sys.argv and first_process():
    time.sleep(60)

turn = 0
for line in sys.stdin:
    if json.loads(line).get("type") != "user":
        continue  # a stray control response
    msg = json.loads(line)["message"]
    texts = [b["text"] for b in msg["content"] if b["type"] == "text"]
    images = sum(1 for b in msg["content"] if b["type"] == "image")
    turn += 1
    if "--fake-freeze" in sys.argv and first_process():
        time.sleep(60)
    if "--fake-lost-session" in sys.argv and resumed:
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": "lost-999",
              "errors": [f"No conversation found with session ID: {sid}"]})
        continue
    emit({"type": "system", "subtype": "init", "session_id": sid, "slash_commands": ["compact", "context", "blake-testing"],
          "skills": ["blake-testing"]})
    if any("PERMIT" in t for t in texts):
        tool = "AskUserQuestion" if any("ASK" in t for t in texts) else "mcp__Shopify__update-product"
        data = ({"questions": [{"question": "Which colour?", "options": [{"label": "Red"}, {"label": "Blue"}]}]}
                if tool == "AskUserQuestion" else {"id": "gid://shopify/Product/1", "title": "New title"})
        emit({"type": "control_request", "request_id": f"req-{turn}",
              "request": {"subtype": "can_use_tool", "tool_name": tool, "input": data, "tool_use_id": "tu9"}})
        answer = json.loads(sys.stdin.readline())["response"]
        decided = answer["response"]
        said = decided["behavior"] + (" " + json.dumps(decided["updatedInput"].get("answers")) if decided.get("updatedInput", {}).get("answers") else "")
        emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": f"Permission: {said}"}]}})
        emit({"type": "result", "subtype": "success", "result": f"Permission: {said}", "session_id": sid})
        continue
    if any("STEP" in t for t in texts):
        emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_use", "id": "tb1", "name": "Bash", "input": {"command": "bin/jarvis task list", "description": "List tasks"}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tb1", "content": "T1 [todo] hello", "is_error": False}]}})
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
    if "--fake-chatty" in sys.argv:
        emit({"type": "system", "subtype": "notice", "text": "x" * 1_000_000})
