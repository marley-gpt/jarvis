"""The team: sub-agents for hard jobs, and hiring new permanent agents (owner decision D36, 2026-11-05).

Sub-agents. Claude Code subagents can't start subagents of their own (the Agent tool is only in the lead
session), so a lead agent that needs help writes the sub-jobs down (`team delegate`) and JARVIS, or a
scheduled run, hands each to a fresh agent run (`team next`, `team start`, `team done`). Temporary sub-agents
need no permission; every one is logged with its parent, job and result, so the dashboard can show them.

Hiring. An agent that keeps meeting a recurring job within its role drafts a new permanent agent
(`team propose`). The rules (D36):
- within the team's existing role, only tools the team already has, costing nothing: JARVIS reviews the draft
  against CLAUDE.md, adds it, and the owner is told;
- new tools, connectors or paid services, or a role outside the team: an `approval request other` goes to the
  owner first, and the agent is added only once it's approved.
No agent can change an existing agent's tools. Every addition is logged and can be undone (`team remove`
archives the agent file into the brain, never deletes it).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import memory, paths, records, skills

SCHEMA = """
CREATE TABLE IF NOT EXISTS sub_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    parent TEXT NOT NULL,
    child TEXT NOT NULL,
    job TEXT NOT NULL,
    task TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    started TEXT,
    finished TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS hires (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    proposer TEXT NOT NULL,
    name TEXT NOT NULL,
    team TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    tools TEXT NOT NULL,
    skills TEXT,
    cost TEXT NOT NULL DEFAULT 'free',
    why TEXT,
    draft TEXT NOT NULL,
    tier TEXT NOT NULL,
    tier_reasons TEXT,
    approval_id INTEGER,
    status TEXT NOT NULL DEFAULT 'proposed',
    decided_by TEXT,
    decided_ts TEXT,
    note TEXT,
    notified TEXT
);
"""

# Teams from the T54 plan (section 7); hired agents join the team they were hired into.
TEAMS = {
    "research": ("product-research", "market-research", "creative-research"),
    "creative": ("creative-director",),
    "store": ("shopify", "store-qa"),
    "ads": ("meta-ads", "meta-ads-auditor"),
    "testing": ("product-testing",),
    "money": ("accountant",),
    "learning": ("mentor-learning",),
    "operations": ("operations",),
}
SUB_STATUSES = ("queued", "running", "done", "failed")
MODELS = ("inherit", "haiku", "sonnet", "opus")
NAME = re.compile(r"^[a-z][a-z0-9-]{2,40}$")


class TeamError(Exception):
    pass


def ensure(conn) -> None:
    conn.executescript(SCHEMA)


def team_of(conn, agent: str) -> str | None:
    for team, members in TEAMS.items():
        if agent in members:
            return team
    ensure(conn)
    row = conn.execute("SELECT team FROM hires WHERE name = ? AND status = 'added'", (agent,)).fetchone()
    return row["team"] if row else None


def members(conn, team: str) -> list[str]:
    ensure(conn)
    hired = [r["name"] for r in conn.execute("SELECT name FROM hires WHERE team = ? AND status = 'added'", (team,))]
    return [a for a in (*TEAMS.get(team, ()), *hired) if a in skills.agent_names()]


def agent_tools(agent: str) -> set[str]:
    meta, _ = memory.parse((skills.agents_dir() / f"{agent}.md").read_text(encoding="utf-8"))
    return {t.strip() for t in str(meta.get("tools", "")).split(",") if t.strip()}


def team_tools(conn, team: str) -> set[str]:
    return set().union(*(agent_tools(a) for a in members(conn, team))) if members(conn, team) else set()


# ---------------------------------------------------------------- sub-agents

def _sub_id(value) -> int:
    text = str(value).upper().lstrip("S")
    if not text.isdigit():
        raise TeamError(f"Bad sub-agent id '{value}' (expected e.g. S3)")
    return int(text)


def delegate(conn, parent: str, job: str, *, child: str | None = None, task=None) -> int:
    """A lead agent hands part of a hard job to a fresh agent run. No permission needed; it's logged."""
    ensure(conn)
    skills.check_agent(parent)
    child = skills.check_agent(child) if child else parent
    if task:
        records.parse_task_id(task)
    cur = conn.execute("INSERT INTO sub_agents (ts, parent, child, job, task) VALUES (?,?,?,?,?)",
                       (paths.now(), parent, child, job.strip(), task))
    conn.commit()
    return cur.lastrowid


def sub_update(conn, sub_id, status: str, result: str | None = None) -> None:
    ensure(conn)
    if status not in SUB_STATUSES:
        raise TeamError(f"Status must be one of: {', '.join(SUB_STATUSES)}")
    sid = _sub_id(sub_id)
    if not conn.execute("SELECT 1 FROM sub_agents WHERE id = ?", (sid,)).fetchone():
        raise TeamError(f"No sub-agent S{sid}")
    col = "started" if status == "running" else "finished"
    conn.execute(f"UPDATE sub_agents SET status = ?, {col} = ?, result = COALESCE(?, result) WHERE id = ?",
                 (status, paths.now(), result, sid))
    conn.commit()


def queued(conn, limit: int | None = None) -> list:
    ensure(conn)
    return conn.execute("SELECT * FROM sub_agents WHERE status = 'queued' ORDER BY id LIMIT ?",
                        (limit if limit is not None else -1,)).fetchall()


# ---------------------------------------------------------------- hiring

def _hire_id(value) -> int:
    text = str(value).upper().lstrip("H")
    if not text.isdigit():
        raise TeamError(f"Bad hire id '{value}' (expected e.g. H2)")
    return int(text)


def proposals_dir() -> Path:
    return paths.home() / "agents" / "proposals"


def removed_dir() -> Path:
    return paths.home() / "agents" / "removed"


def propose(conn, proposer: str, name: str, *, role: str, body: str, tools: list[str], model="sonnet",
            skill_list=None, team=None, cost="free", why=None) -> tuple[int, str, list[str]]:
    """Draft a new permanent agent. Returns (hire id, tier, reasons it needs the owner)."""
    ensure(conn)
    skills.check_agent(proposer)
    home_team = team_of(conn, proposer)
    team = team or home_team
    if not NAME.match(name):
        raise TeamError("Name it in lower-case-with-dashes, e.g. 'review-miner'.")
    if name in skills.agent_names() or conn.execute(
            "SELECT 1 FROM hires WHERE name = ? AND status IN ('proposed', 'added')", (name,)).fetchone():
        raise TeamError(f"'{name}' already exists or is already proposed. Agents can't rewrite existing agents.")
    if model not in MODELS:
        raise TeamError(f"Model must be one of: {', '.join(MODELS)}")
    if team not in TEAMS:
        raise TeamError(f"Team must be one of: {', '.join(TEAMS)}")
    if not tools:
        raise TeamError("List the tools it needs (--tools).")
    reasons = []
    if team != home_team:
        reasons.append(f"a role outside {proposer}'s team ({home_team} -> {team})")
    new_tools = sorted(set(tools) - team_tools(conn, home_team or team))
    if new_tools:
        reasons.append("new tools or connectors: " + ", ".join(new_tools))
    if not skills.is_free(cost):
        reasons.append(f"costs money: {cost}")
    tier = "owner" if reasons else "jarvis"
    draft = proposals_dir() / f"{name}.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    skill_names = ["jarvis-memory", "jarvis-feedback-loop"] + [s for s in skill_list or [] if s not in
                                                              ("jarvis-memory", "jarvis-feedback-loop")]
    draft.write_text(
        f"---\nname: {name}\ndescription: {role.strip()}\ntools: {', '.join(tools)}\nmodel: {model}\n"
        f"skills: [{', '.join(skill_names)}]\n---\n\n{body.strip()}\n\n"
        f"## Team\nPart of the {team} team, hired on {paths.now()[:10]} at {proposer}'s proposal. Follow the "
        "`jarvis-memory` skill: Blake's methods first, record everything, approvals still apply, and never "
        "change your own tools or another agent's.\n", encoding="utf-8")
    approval = None
    if tier == "owner":
        approval = records.approval_request(conn, "other", f"New agent '{name}' for the {team} team: "
                                            + "; ".join(reasons), detail=f"{role}\nWhy: {why or '-'}\nDraft: "
                                            f"{paths.rel(draft)}")
    cur = conn.execute(
        "INSERT INTO hires (ts, proposer, name, team, role, model, tools, skills, cost, why, draft, tier, "
        "tier_reasons, approval_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (paths.now(), proposer, name, team, role, model, ",".join(tools), ",".join(skill_names), cost or "free",
         why, paths.rel(draft), tier, "; ".join(reasons), approval))
    conn.commit()
    return cur.lastrowid, tier, reasons


def hire_get(conn, hire_id):
    ensure(conn)
    row = conn.execute("SELECT * FROM hires WHERE id = ?", (_hire_id(hire_id),)).fetchone()
    if not row:
        raise TeamError(f"No hire {hire_id}")
    return row


def _tell_owner(text: str, notify) -> str:
    if notify is None:
        from . import telegram
        notify = telegram.alert
    try:
        notify(text)
        return "told"
    except Exception as exc:  # the phone being unreachable mustn't undo a reviewed decision
        return f"not told: {exc}"


def approve(conn, hire_id, *, by: str, note: str = "", notify=None) -> str:
    """Add the agent. JARVIS may approve within-role, free hires; the rest need the owner's approval first."""
    row = hire_get(conn, hire_id)
    if row["status"] != "proposed":
        raise TeamError(f"H{row['id']} is already {row['status']}")
    if by not in skills.REVIEWERS:
        raise TeamError(f"--by must be one of: {', '.join(skills.REVIEWERS)}")
    if row["tier"] == "owner":
        a = conn.execute("SELECT status FROM approvals WHERE id = ?", (row["approval_id"],)).fetchone()
        if not a or a["status"] != "approved":
            raise TeamError(f"H{row['id']} needs the owner's approval first (A{row['approval_id']}: "
                            f"{a['status'] if a else 'missing'}). {row['tier_reasons']}")
    dest = skills.agents_dir() / f"{row['name']}.md"
    if dest.exists():
        raise TeamError(f"{paths.rel(dest)} already exists")
    shutil.copyfile(paths.REPO_ROOT / row["draft"], dest)  # an absolute draft path stays absolute
    told = _tell_owner(f"New agent added: {row['name']} ({row['team']} team), proposed by {row['proposer']}: "
                       f"{row['role'][:140]}. Undo with: bin/jarvis team remove {row['name']}", notify)
    conn.execute("UPDATE hires SET status = 'added', decided_by = ?, decided_ts = ?, note = ?, notified = ? "
                 "WHERE id = ?", (by, paths.now(), note, told, row["id"]))
    conn.commit()
    return f"H{row['id']} added: {paths.rel(dest)} (owner {told})"


def reject(conn, hire_id, *, by: str, note: str = "") -> str:
    row = hire_get(conn, hire_id)
    if row["status"] != "proposed":
        raise TeamError(f"H{row['id']} is already {row['status']}")
    conn.execute("UPDATE hires SET status = 'rejected', decided_by = ?, decided_ts = ?, note = ? WHERE id = ?",
                 (by, paths.now(), note, row["id"]))
    conn.commit()
    return f"H{row['id']} rejected"


def remove(conn, name: str, *, by: str, why: str) -> str:
    """Undo a hire: archive the agent file into the brain. Only agents added through hiring can be removed."""
    ensure(conn)
    row = conn.execute("SELECT * FROM hires WHERE name = ? AND status = 'added'", (name,)).fetchone()
    if not row:
        raise TeamError(f"'{name}' wasn't added through hiring, so it can't be removed here.")
    src = skills.agents_dir() / f"{name}.md"
    dest = removed_dir() / f"{name}-{paths.now()[:10]}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), dest)
    conn.execute("UPDATE hires SET status = 'removed', note = COALESCE(note, '') || ? WHERE id = ?",
                 (f" | removed {paths.now()[:10]} by {by}: {why}", row["id"]))
    conn.commit()
    return f"{name} removed (archived to {paths.rel(dest)})"


# ---------------------------------------------------------------- dashboard data

def board(conn) -> dict:
    from . import pace
    ensure(conn)
    names = set(skills.agent_names())
    teams = []
    for team in TEAMS:
        hired = [dict(r) for r in conn.execute("SELECT id, name, role, proposer, ts, decided_by FROM hires "
                                                "WHERE team = ? AND status = 'added'", (team,))]
        teams.append({"name": team, "agents": [a for a in TEAMS[team] if a in names],
                      "hired": [{**h, "id": f"H{h['id']}"} for h in hired]})
    subs = [{**dict(r), "id": f"S{r['id']}"} for r in conn.execute(
        "SELECT * FROM sub_agents ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
        "id DESC LIMIT 40")]
    hires = [{**dict(r), "id": f"H{r['id']}", "approval_id": r["approval_id"] and f"A{r['approval_id']}"}
             for r in conn.execute("SELECT * FROM hires ORDER BY id DESC LIMIT 20")]
    return {"generated": paths.now(), "teams": teams,
            "sub_agents": {"running": [s for s in subs if s["status"] == "running"],
                           "queued": [s for s in subs if s["status"] == "queued"],
                           "recent": [s for s in subs if s["status"] in ("done", "failed")][:15]},
            "hires": {"proposed": [h for h in hires if h["status"] == "proposed"], "recent": hires},
            "pace": pace.status(conn)}
