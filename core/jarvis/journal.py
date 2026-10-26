"""Conversation journal: every owner message and JARVIS reply, captured by hooks.

Long-term knowledge (memory.py) depends on JARVIS choosing to save it. The journal
does not: hooks record each exchange, so nothing the owner says is lost even when
it was never turned into a memory entry. It is searchable (`bin/jarvis recall`) and
mirrored to readable daily Markdown files in brain/journal/.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import memory, paths

ROLES = ("owner", "jarvis")
MAX_CHARS = 6000

# Credentials pasted into chat are blanked before anything is stored.
SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|sk_(live|test)_[A-Za-z0-9]{16,}|shp(at|ca|pa|ss)_[A-Fa-f0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|EAA[A-Za-z0-9]{40,}|AIza[A-Za-z0-9_-]{30,}"
    r"|xox[abpr]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
SECRET_ASSIGN = re.compile(r"(?i)\b(password|passwd|api[_ -]?key|secret|token)(\s*[:=]\s*)(\S{6,})")

# Machine text that arrives as a prompt: background-task notices, and context blocks the app wraps
# around the owner's words (e.g. <artifact-view-context>). Only the owner's own words are journaled.
NOTICE = re.compile(r"^\s*(<task-notification>|\[SYSTEM NOTIFICATION)", re.I)
TAG_BLOCK = re.compile(r"<([a-z][\w-]*)\b[^>]*>.*?</\1>|<[a-z][\w-]*\b[^>]*/>", re.I | re.S)


def owner_words(prompt: str) -> str:
    """The part of a prompt the owner actually wrote ('' for a system notice)."""
    if NOTICE.match(prompt or ""):
        return ""
    return TAG_BLOCK.sub("", prompt or "").strip()


def redact(text: str) -> str:
    text = SECRET.sub("[redacted]", text)
    return SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)


def record(conn, role: str, text: str, session_id: str | None = None) -> int | None:
    """Store one message. Returns its id, or None if empty or already stored for this session."""
    if role not in ROLES:
        raise ValueError(f"Unknown journal role '{role}'")
    text = redact((text or "").strip())
    if not text:
        return None
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " …[truncated]"
    digest = hashlib.sha1(f"{role}\n{text}".encode()).hexdigest()
    session = session_id or ""
    if conn.execute("SELECT 1 FROM journal WHERE session_id = ? AND digest = ?", (session, digest)).fetchone():
        return None
    ts = paths.now()
    cur = conn.execute("INSERT INTO journal (ts, session_id, role, text, digest) VALUES (?,?,?,?,?)",
                       (ts, session, role, text, digest))
    conn.execute("INSERT INTO journal_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
    conn.commit()
    _append_markdown(ts, role, text, session)
    return cur.lastrowid


def _append_markdown(ts: str, role: str, text: str, session: str) -> None:
    day = paths.journal_dir() / f"{ts[:10]}.md"
    day.parent.mkdir(parents=True, exist_ok=True)
    head = "" if day.exists() else f"# JARVIS journal {ts[:10]}\n"
    who = "Owner" if role == "owner" else "JARVIS"
    with day.open("a", encoding="utf-8") as f:
        f.write(f"{head}\n### {ts[11:16]} {who}" + (f" · session {session[:8]}" if session else "") + "\n\n")
        f.write(text + "\n")


def search(conn, text: str, *, limit=5, exclude_session: str | None = None, role: str | None = None):
    filters, params = [], []
    if exclude_session:
        filters.append("j.session_id != ?")
        params.append(exclude_session)
    if role:
        filters.append("j.role = ?")
        params.append(role)
    where = (" AND " + " AND ".join(filters)) if filters else ""
    for mode in ("AND", "OR"):
        q = memory._fts_query(text, mode)
        if not q:
            break
        rows = conn.execute(
            f"""SELECT j.*, snippet(journal_fts, 0, '[', ']', ' … ', 16) AS snippet
                FROM journal_fts JOIN journal j ON j.id = journal_fts.rowid
                WHERE journal_fts MATCH ?{where}
                ORDER BY bm25(journal_fts) LIMIT ?""",
            [q, *params, limit],
        ).fetchall()
        if rows:
            return rows
    return []


def recent(conn, *, limit=20, role=None, session_id=None):
    filters, params = [], []
    if role:
        filters.append("role = ?")
        params.append(role)
    if session_id:
        filters.append("session_id = ?")
        params.append(session_id)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = conn.execute(f"SELECT * FROM journal {where} ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
    return list(reversed(rows))


def previous_session(conn, current: str | None = None) -> str | None:
    row = conn.execute("SELECT session_id FROM journal WHERE session_id != ? ORDER BY id DESC LIMIT 1",
                       (current or "",)).fetchone()
    return row[0] if row else None


def last_reply(transcript_path: str | None) -> str:
    """Text of JARVIS's final reply in a Claude Code transcript (JSONL), or '' if unreadable."""
    if not transcript_path:
        return ""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    parts: list[str] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("isSidechain"):
            continue
        msg = item.get("message") or {}
        content = msg.get("content")
        if item.get("type") == "user":
            # A tool result belongs to the same turn; a real prompt ends the search.
            if isinstance(content, str) or any(isinstance(c, dict) and c.get("type") == "text" for c in content or []):
                break
            continue
        if item.get("type") == "assistant" and isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if any(t.strip() for t in texts):
                parts.insert(0, "\n".join(t for t in texts if t.strip()))
    return "\n\n".join(parts)


def short(text: str, n: int = 140) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def when(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts[:16]
