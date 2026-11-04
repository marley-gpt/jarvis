"""The agent activity log: one row for everything an agent starts, does and finishes.

Every visual on the HQ dashboard (the office floor, the activity stream, agent profiles, and the
"Monaco Estate" 3D world, whose characters only move on real events) reads from here. The hooks write most of it automatically (a subagent starting and stopping, each tool
it uses and the file it touched, who it hands work to), task records add assigned/done/blocked as tasks change,
and agents and JARVIS can add their own with `bin/jarvis event add`. Every row is a real event: nothing
here is ever invented to make the world look busy. `kind` is "agent" today; business data (orders,
spend, test verdicts) will arrive as other kinds later.

    bin/jarvis event add <agent> '<action>' [--status working] [--task T3] [-p slug] [--tool X] [--file F]
    bin/jarvis event list [--agent X] [-n 20]
"""

from __future__ import annotations

from datetime import datetime

from . import paths

STATUSES = ("assigned", "started", "working", "collaborating", "waiting", "done", "blocked", "error")
ACTIVE = ("started", "working", "collaborating")

# Departments (buildings in the HQ world) for the 12 specialists; JARVIS runs the executive HQ.
DEPARTMENTS = {
    "jarvis": "executive",
    "mentor-learning": "knowledge",
    "product-research": "research", "market-research": "research", "creative-research": "research",
    "creative-director": "creative",
    "shopify": "store", "store-qa": "store",
    "meta-ads": "ads", "meta-ads-auditor": "ads",
    "product-testing": "testing",
    "operations": "operations", "accountant": "finance",
}


def department(agent) -> str | None:
    return DEPARTMENTS.get(agent or "")
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
        file=None, summary=None, session_id=None, with_agent=None, kind: str = "agent") -> int:
    if status not in STATUSES:
        raise ActivityError(f"Status must be one of: {', '.join(STATUSES)}")
    agent = (agent or "").strip() or "jarvis"
    action = _short(action, 200)
    if not action:
        raise ActivityError("An event needs an action.")
    cur = conn.execute(
        "INSERT INTO agent_events (ts, kind, agent, department, task_id, product, action, tool, file, status,"
        " with_agent, summary, session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (paths.now(), kind, agent, department(agent), _task_id(task), product or None, action, tool or None,
         file or None, status, with_agent or None, _short(summary) or None, session_id))
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
    """Each agent's latest event, with `state` = its status, or "idle" once it's done or gone quiet.
    "assigned" stays as it is: the task is waiting for the agent, which is real and worth showing."""
    now = now or datetime.now().astimezone()
    rows = conn.execute("SELECT e.* FROM agent_events e JOIN (SELECT agent, MAX(id) AS id FROM agent_events"
                        " WHERE kind = 'agent' GROUP BY agent) last ON e.id = last.id ORDER BY e.id DESC").fetchall()
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


TASK_STATES = {"in_progress": "working", "blocked": "blocked", "waiting_approval": "waiting", "done": "done",
               "cancelled": "done"}


def task_changed(conn, task_id, agent, product, title, status) -> None:
    """Called by records when a task is created or changes status. Never raises."""
    try:
        if status == "todo":
            if agent:
                add(conn, agent, f"assigned: {title}", status="assigned", task=task_id, product=product)
        elif status in TASK_STATES:
            add(conn, agent or "jarvis", f"{status.replace('_', ' ')}: {title}", status=TASK_STATES[status],
                task=task_id, product=product)
    except Exception:  # noqa: BLE001 - the activity log must never break a task record
        pass


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
