"""The agent activity log: one row for everything an agent starts, does and finishes.

Every visual on the HQ dashboard (the office floor, the activity stream, agent profiles) reads
from here. The hooks write most of it automatically (a subagent starting and stopping, each tool
it uses and the file it touched); agents and JARVIS can add their own with `bin/jarvis event add`.

    bin/jarvis event add <agent> '<action>' [--status working] [--task T3] [-p slug] [--tool X] [--file F]
    bin/jarvis event list [--agent X] [-n 20]
"""

from __future__ import annotations

from datetime import datetime

from . import paths

STATUSES = ("started", "working", "waiting", "done", "blocked")
ACTIVE = ("started", "working")
STALE_SECONDS = 30 * 60  # "working" with no event for this long shows as idle


class ActivityError(Exception):
    pass


def _task_id(task):
    if task in (None, ""):
        return None
    try:
        return int(str(task).upper().lstrip("T"))
    except ValueError as exc:
        raise ActivityError(f"Not a task id: {task}") from exc


def _short(text, n=300):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def add(conn, agent: str, action: str, *, status: str = "working", task=None, product=None, tool=None,
        file=None, summary=None, session_id=None) -> int:
    if status not in STATUSES:
        raise ActivityError(f"Status must be one of: {', '.join(STATUSES)}")
    agent = (agent or "").strip() or "jarvis"
    action = _short(action, 200)
    if not action:
        raise ActivityError("An event needs an action.")
    cur = conn.execute(
        "INSERT INTO agent_events (ts, agent, task_id, product, action, tool, file, status, summary, session_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (paths.now(), agent, _task_id(task), product or None, action, tool or None, file or None, status,
         _short(summary) or None, session_id))
    conn.commit()
    return cur.lastrowid


def recent(conn, limit: int = 50, agent=None, since_id: int = 0) -> list[dict]:
    """Newest first. `since_id` lets a poller fetch only what it hasn't seen."""
    sql, params = "SELECT * FROM agent_events WHERE id > ?", [since_id]
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def current(conn, now: datetime | None = None) -> dict[str, dict]:
    """Each agent's latest event, with `state` = its status, or "idle" once it's done or gone quiet."""
    now = now or datetime.now().astimezone()
    rows = conn.execute("SELECT e.* FROM agent_events e JOIN (SELECT agent, MAX(id) AS id FROM agent_events"
                        " GROUP BY agent) last ON e.id = last.id ORDER BY e.id DESC").fetchall()
    out = {}
    for r in rows:
        ev = dict(r)
        state = ev["status"]
        if state == "done":
            state = "idle"
        elif state in ACTIVE:
            try:
                if (now - datetime.fromisoformat(ev["ts"])).total_seconds() > STALE_SECONDS:
                    state = "idle"
            except (TypeError, ValueError):
                pass
        out[ev["agent"]] = {**ev, "state": state}
    return out


def tool_target(tool_input) -> tuple[str | None, str | None]:
    """(file, summary) for a tool call: the file it touched, or a short note of what it ran."""
    if not isinstance(tool_input, dict):
        return None, None
    for key in ("file_path", "notebook_path", "path"):
        if tool_input.get(key):
            return str(tool_input[key]), None
    for key in ("command", "url", "pattern", "query", "skill", "description"):
        if tool_input.get(key):
            return None, _short(tool_input[key], 160)
    return None, None
