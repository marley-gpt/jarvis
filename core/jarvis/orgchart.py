"""The team map (T109; the Team page, `/team`): JARVIS as the conductor, the twelve specialists by department.

After the owner's reel (Bennett Spooner, M-261110-3abc) and decision D73. Read-only over the brain and
`.claude/agents/`. For each agent: what its file says it is for, the skills it preloads and the playbooks its
`## Skills` section names (only ones that are installed), its standing procedure (the rest of its file, section by
section), its open tasks and its last finished one. Hand-offs are worked out from the tasks table: inside one parent
task, a subtask that started after another agent's sibling finished took that agent's work forward. What each agent is
doing this minute is not here; the page reads it from the `live` section of the app's state (swarm.py, D70).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from . import memory, paths, records, roster

AGENTS_DIR = paths.REPO_ROOT / ".claude" / "agents"
SKILL_DIRS = (paths.REPO_ROOT / ".claude" / "skills", Path.home() / ".claude" / "skills")

# The owner's six departments (T109). Order is the order round the ring, clockwise from the top.
DEPARTMENTS = [
    {"id": "research", "name": "Research", "agents": ["product-research", "market-research", "mentor-learning"],
     "job": "Finds products, reads customers and keeps Blake's methods to hand."},
    {"id": "creative", "name": "Creative", "agents": ["creative-research", "creative-director"],
     "job": "Studies winning ads, then turns research into statics and copy."},
    {"id": "store", "name": "Store", "agents": ["shopify", "store-qa"],
     "job": "Drafts product pages and checks the store like a customer."},
    {"id": "ads", "name": "Ads", "agents": ["meta-ads", "meta-ads-auditor", "product-testing"],
     "job": "Builds Meta campaigns, audits the account and calls each test."},
    {"id": "money", "name": "Money", "agents": ["accountant"],
     "job": "Every cost and sale against the A$380 a week target."},
    {"id": "ops", "name": "Ops", "agents": ["operations"],
     "job": "Status, bottlenecks, priorities and the daily overview."},
]

LABELS = {
    "mentor-learning": "Mentor Notes", "product-research": "Product Research", "market-research": "Customer & Market",
    "creative-research": "Creative Research", "creative-director": "Creative Director", "shopify": "Shopify",
    "store-qa": "Store QA", "meta-ads": "Meta Ads", "meta-ads-auditor": "Meta Auditor",
    "product-testing": "Product Testing", "operations": "Operations", "accountant": "Accountant",
}
SOP_MAX = 9000


def _installed() -> set[str]:
    out: set[str] = set()
    for d in SKILL_DIRS:
        if d.exists():
            out |= {p.name for p in d.iterdir() if p.is_dir()}
    return out


def _sections(body: str) -> list[dict]:
    """The agent file's body as titled sections; the untitled opening is its role."""
    out, title, lines = [], "Role", []
    for line in body.strip().splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if "".join(lines).strip():
                out.append({"title": title, "text": "\n".join(lines).strip()})
            title, lines = m.group(1).strip(), []
        else:
            lines.append(line)
    if "".join(lines).strip():
        out.append({"title": title, "text": "\n".join(lines).strip()})
    return out


def profile(name: str, installed: set[str] | None = None) -> dict | None:
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        return None
    meta, body = memory.parse(path.read_text(encoding="utf-8"))
    installed = _installed() if installed is None else installed
    sections = _sections(body)
    core = roster.skills_for(meta, body)
    playbooks = []
    for s in sections:
        if s["title"].lower() == "skills":
            for found in re.findall(r"`([a-z0-9][a-z0-9:-]+)`", s["text"]):
                if found in installed and found not in core and found not in playbooks:
                    playbooks.append(found)
    sop, used = [], 0
    for s in sections:
        if s["title"].lower() == "skills" or used >= SOP_MAX:
            continue
        text = s["text"][: SOP_MAX - used]
        used += len(text)
        sop.append({"title": s["title"], "text": text})
    tools = roster._list(meta.get("tools"))
    return {"name": name, "label": LABELS.get(name, name.replace("-", " ").title()),
            "description": str(meta.get("description") or "").strip(), "model": roster.model_alias(meta.get("model")),
            "skills": core, "playbooks": playbooks, "connections": roster.connections(tools), "sop": sop}


def _handoffs(conn, days: int = 7, limit: int = 16) -> list[dict]:
    """Work passed from one agent to the next inside the same parent task, newest last."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("SELECT id, parent_id, agent, title, created, completed FROM tasks WHERE parent_id IN"
                        " (SELECT DISTINCT parent_id FROM tasks WHERE parent_id IS NOT NULL AND created >= ?)"
                        " AND agent IS NOT NULL ORDER BY created", (since,)).fetchall()
    by_parent: dict = {}
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(dict(r))
    out = []
    for parent, subs in by_parent.items():
        for b in subs:
            if (b["created"] or "") < since:
                continue
            before = [a for a in subs if a["agent"] != b["agent"] and a["completed"] and a["completed"] <= b["created"]]
            if before:
                a = max(before, key=lambda x: x["completed"])
                out.append({"from": a["agent"], "to": b["agent"], "at": b["created"], "parent": f"T{parent}",
                            "via": f"T{a['id']}", "task": f"T{b['id']}", "title": b["title"]})
    out.sort(key=lambda h: h["at"])
    return out[-limit:]


def state(conn) -> dict:
    installed = _installed()
    names = [n for d in DEPARTMENTS for n in d["agents"]]
    on_disk = sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
    marks = ",".join("?" * len(records.OPEN_TASK_STATUSES))
    open_rows = conn.execute(f"SELECT id, title, agent, status, product, updated FROM tasks WHERE status IN ({marks})"
                             " AND agent IS NOT NULL ORDER BY (status = 'in_progress') DESC, updated DESC",
                             records.OPEN_TASK_STATUSES).fetchall()
    agents = {}
    for name in names + [n for n in on_disk if n not in names]:
        p = profile(name, installed)
        if not p:
            continue
        p["open"] = [{"id": f"T{r['id']}", "title": r["title"], "status": r["status"], "product": r["product"]}
                     for r in open_rows if r["agent"] == name][:6]
        last = conn.execute("SELECT id, title, completed, deliverable FROM tasks WHERE agent = ? AND status = 'done'"
                            " ORDER BY completed DESC LIMIT 1", (name,)).fetchone()
        p["last_done"] = ({"id": f"T{last['id']}", "title": last["title"], "completed": last["completed"],
                           "deliverable": last["deliverable"]} if last else None)
        p["playbook_notes"] = conn.execute("SELECT COUNT(*) FROM memory WHERE status = 'active' AND tags LIKE ?",
                                           (f"%for:{name}%",)).fetchone()[0]
        agents[name] = p
    departments = [{**d, "agents": [a for a in d["agents"] if a in agents]} for d in DEPARTMENTS]
    extra = [n for n in agents if n not in names]
    if extra:   # a new hire lands somewhere visible until it is given a department
        departments.append({"id": "new", "name": "New hires", "agents": extra, "job": "Agents without a department yet."})
    cats = conn.execute("SELECT category, COUNT(*) n FROM memory WHERE status = 'active' GROUP BY category"
                        " ORDER BY n DESC").fetchall()
    newest = conn.execute("SELECT id, title, updated FROM memory WHERE status = 'active' ORDER BY updated DESC LIMIT 1"
                          ).fetchone()
    jarvis_open = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({marks}) AND parent_id IS NULL",
                               records.OPEN_TASK_STATUSES).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0]
    return {
        "departments": departments, "agents": agents, "handoffs": _handoffs(conn),
        "jarvis": {"open_projects": jarvis_open, "approvals_pending": pending,
                   "role": "Conductor: takes the owner's request, hands the jobs out, checks the work against "
                           "Blake's methods and asks before anything that spends or launches."},
        "memory": {"total": sum(r["n"] for r in cats), "categories": [{"name": r["category"], "n": r["n"]} for r in cats[:8]],
                   "newest": dict(newest) if newest else None},
    }
