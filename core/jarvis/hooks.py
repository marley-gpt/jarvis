"""Claude Code hook handlers (wired up in .claude/settings.json via bin/hook).

session_start  inject the JARVIS brief, initialise the brain, take the daily backup
guard          PreToolUse: protect brain data and gate consequential external actions
post_tool      PostToolUse: append consequential MCP calls to the action log
subagent       SubagentStart/SubagentStop: record which specialist ran
"""

from __future__ import annotations

import json
import re
import sys

from . import backup, brief, cli, db, paths, records

# MCP tool names whose last segment starts with one of these verbs are read-only.
READ_VERBS = ("get", "list", "search", "query", "show", "find", "read", "fetch", "validate", "describe",
              "explore", "balance", "schema", "resolve", "status", "test", "transactions", "job")
WRITE_WORDS = {"create", "update", "delete", "set", "publish", "unpublish", "bulk", "mutation", "trash", "share",
               "generate", "upload", "deploy", "remove", "archive", "cancel", "send", "post", "launch", "pause",
               "add", "move", "rename", "copy", "execute", "invoke", "exec", "register", "confirm", "connect",
               "import", "write", "edit", "merge", "submit", "pay", "buy", "order", "spend", "budget"}

PROTECTED_FILES = re.compile(r"jarvis\.db(-wal|-shm)?$|[\\/]brain[\\/]backups[\\/]", re.I)
DESTRUCTIVE_CMD = re.compile(r"(^|[\s;&|(])(rm|rmdir|del|erase|unlink|shred|truncate|mv|move|git\s+rm|git\s+clean)\b",
                             re.I)
BRAIN_REF = re.compile(r"(?<![\w.-])brain(?![\w.-])([\\/][^\s'\";&|]*)?", re.I)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
SQL_WRITE = re.compile(r"\b(drop\s+table|delete\s+from|update\s+\w+\s+set|insert\s+into|vacuum)\b", re.I)


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
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

    if tool.startswith("mcp__") and not is_read_only_tool(tool):
        server = tool.split("__")[1] if tool.count("__") >= 2 else tool
        return _ask(f"JARVIS approval gate: {tool} can change {server} or spend credits. "
                    "Approve only if the user has agreed to this specific action.")
    return None


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
            sys.stdout.write(brief.session_brief(conn) + "\n")
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
