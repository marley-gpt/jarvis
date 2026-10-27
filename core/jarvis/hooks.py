"""Claude Code hook handlers (wired up in .claude/settings.json via bin/hook).

session_start  inject the JARVIS brief, initialise the brain, take the daily backup
prompt         UserPromptSubmit: journal the owner's message and inject relevant memory (auto-recall)
stop           Stop: journal JARVIS's reply
guard          PreToolUse: protect brain data and gate consequential external actions
post_tool      PostToolUse: append consequential MCP calls to the action log
subagent       SubagentStart/SubagentStop: record which specialist ran
"""

from __future__ import annotations

import json
import re
import sys

from . import backup, brief, cli, db, journal, memory, paths, records

# MCP tool names whose last segment starts with one of these verbs are read-only.
READ_VERBS = ("get", "list", "search", "query", "show", "find", "read", "fetch", "validate", "describe",
              "explore", "balance", "schema", "resolve", "status", "test", "transactions", "job")
WRITE_WORDS = {"create", "update", "delete", "set", "publish", "unpublish", "bulk", "mutation", "trash", "share",
               "generate", "upload", "deploy", "remove", "archive", "cancel", "send", "post", "launch", "pause",
               "add", "move", "rename", "copy", "execute", "invoke", "exec", "register", "confirm", "connect",
               "import", "write", "edit", "merge", "submit", "pay", "buy", "order", "spend", "budget"}

# MCP servers that only act inside this PC or this app (browser, desktop, terminal, sessions, views).
# The owner gave blanket permission for routine work, so these, and read-only tools on any server, are
# allowed outright: the headless dashboard/Telegram session has nobody to click a prompt, and connector
# names differ between apps (mcp__Shopify__ here, mcp__claude_ai_Shopify__ headless). The gate stays on
# tools that can change the store, ads or shared files or spend credits; headless, its prompt is a refusal.
LOCAL_MCP_SERVERS = re.compile(r"^(claude-in-chrome|Claude_Browser|computer-use|terminal|visualize|scheduled-tasks|"
                               r"mcp-registry|ccd_[a-z_]+)$", re.I)

PROTECTED_FILES = re.compile(r"jarvis\.db(-wal|-shm)?$|[\\/]brain[\\/]backups[\\/]", re.I)
DESTRUCTIVE_CMD = re.compile(r"(^|[\s;&|(])(rm|rmdir|del|erase|unlink|shred|truncate|mv|move|git\s+rm|git\s+clean)\b",
                             re.I)
BRAIN_REF = re.compile(r"(?<![\w.-])brain(?![\w.-])([\\/][^\s'\";&|]*)?", re.I)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
SQL_WRITE = re.compile(r"\b(drop\s+table|delete\s+from|update\s+\w+\s+set|insert\s+into|vacuum)\b", re.I)


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _allow(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "permissionDecisionReason": reason}}


def _ask(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                   "permissionDecisionReason": reason}}


def is_read_only_tool(tool: str) -> bool:
    """Name-based: any write-ish word wins; otherwise a read verb is required. Unknown → not read-only."""
    words = set(re.split(r"[-_]", tool.split("__")[-1].lower()))
    if words & WRITE_WORDS:
        return False
    return bool(words & set(READ_VERBS))


def guard(event: dict) -> dict | None:
    tool = event.get("tool_name", "")
    data = event.get("tool_input") or {}

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = data.get("file_path") or data.get("notebook_path") or ""
        if PROTECTED_FILES.search(target):
            return _deny("JARVIS guard: the database and backups are only changed through bin/jarvis.")
        return None

    if tool == "Bash":
        # Heredoc bodies are file content, not commands.
        cmd = HEREDOC.sub("", data.get("command", ""))
        refs = [m.group(0) for m in BRAIN_REF.finditer(cmd)]
        unsafe_refs = [r for r in refs if not re.match(r"brain[\\/]scratch([\\/]|$)", r, re.I)]
        # Verbs are matched outside quotes so prose like --body "move fast" isn't mistaken for a command.
        unquoted = re.sub(r"'[^']*'|\"(\\.|[^\"\\])*\"", "''", cmd)
        if unsafe_refs and DESTRUCTIVE_CMD.search(unquoted):
            return _deny("JARVIS guard: deleting or moving brain data is blocked. Archive memory with "
                         "`bin/jarvis mem status <id> archived`; only brain/scratch may be cleared.")
        if re.search(r"jarvis\.db", cmd, re.I) and (
                (re.search(r"\bsqlite3?\b", unquoted) and SQL_WRITE.search(cmd))
                or re.search(r">\s*\S*jarvis\.db", unquoted)):
            return _deny("JARVIS guard: write to the database through bin/jarvis, not raw SQL.")
        if re.search(r"\bgit\s+add\b.*(-f\b|--force)", cmd) and refs:
            return _deny("JARVIS guard: brain/ holds private business data and this repository is public. "
                         "It must never be committed.")
        return None

    if tool.startswith("mcp__"):
        server = tool.split("__")[1] if tool.count("__") >= 2 else tool
        if LOCAL_MCP_SERVERS.match(server):
            return _allow("JARVIS: local app tool, covered by the owner's blanket permission.")
        if is_read_only_tool(tool):
            return _allow("JARVIS: read-only tool, covered by the owner's blanket permission.")
        granted = records.grant_active()
        if granted:
            return _allow(f"JARVIS: carrying out A{granted}, which the owner approved.")
        return _ask(f"JARVIS approval gate: {tool} can change {server} or spend credits. "
                    "Approve only if the user has agreed to this specific action.")
    return None


SAVE_NUDGE = ("If this message states a fact, preference, correction, rule or decision, save it now "
              "(`bin/jarvis mem add --provenance user`, `mem supersede <id>` or `decide`) before replying.")


def recall(conn, prompt: str, session_id: str | None) -> str:
    """Compact context block for one owner message: matching memory and earlier conversation."""
    cfg = paths.config()
    lines = []
    words = memory._fts_query(prompt, "OR")
    if words:
        hits = memory.search(conn, prompt, limit=int(cfg["recall_memory_hits"]))
        if hits:
            lines.append("Memory that may be relevant (`bin/jarvis mem get <id>` for the full entry):")
            lines += [f"- {h['id']} [{h['category']}/{h['provenance']}] {h['title']} — {journal.short(h['snippet'], 160)}"
                      for h in hits]
        said = journal.search(conn, prompt, limit=int(cfg["recall_journal_hits"]), exclude_session=session_id)
        if said:
            lines.append("Earlier conversation (`bin/jarvis recall \"...\"` for more):")
            lines += [f"- J{j['id']} {journal.when(j['ts'])} {j['role']}: {journal.short(j['snippet'], 160)}"
                      for j in said]
    head = "JARVIS auto-recall (local search, may be incomplete)."
    return "\n".join([head, *lines, SAVE_NUDGE])


def _summarise(data) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= 400 else text[:400] + "…"


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else ""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    if kind == "guard":
        decision = guard(event)
        if decision:
            sys.stdout.write(json.dumps(decision))
        return 0

    conn = db.connect()
    try:
        if kind == "session_start":
            if not paths.memory_dir().exists():
                cli.cmd_init(conn, None)
            try:
                backup.daily(conn)
            except OSError as exc:  # a failed backup must not block the session
                sys.stderr.write(f"jarvis: daily backup failed: {exc}\n")
            sys.stdout.write(brief.session_brief(conn, session_id=event.get("session_id")) + "\n")
        elif kind == "prompt":
            prompt = journal.owner_words(event.get("prompt") or "")
            if not prompt:  # a background-task notice, not the owner
                return
            session = event.get("session_id")
            context = recall(conn, prompt, session)  # search before recording, so the message can't match itself
            journal.record(conn, "owner", prompt, session)
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                                 "additionalContext": context}}))
        elif kind == "stop":
            reply = event.get("last_assistant_message") or journal.last_reply(event.get("transcript_path"))
            journal.record(conn, "jarvis", reply, event.get("session_id"))
        elif kind == "post_tool":
            tool = event.get("tool_name", "")
            if tool.startswith("mcp__") and not is_read_only_tool(tool):
                records.log_action(conn, tool, _summarise(event.get("tool_input")), event.get("session_id"))
        elif kind == "subagent":
            records.log_agent_run(conn, event.get("agent_type") or event.get("subagent_type"),
                                  event.get("session_id"), event.get("hook_event_name", "subagent"),
                                  event.get("agent_id"))
        else:
            sys.stderr.write(f"jarvis hook: unknown event '{kind}'\n")
            return 1
    finally:
        conn.close()
    return 0
