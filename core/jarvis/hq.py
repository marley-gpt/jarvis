"""Data for the 3D HQ page (`/hq`): the teams, what each agent is connected to, and its work.

The live state (statuses, tasks, approvals) still comes from `/api/state` and the event
stream; this adds what that doesn't carry: the eight teams of T54 (plan section 7), each
agent's tools, connectors and skills from `.claude/agents/*.md`, its recent finished work,
and safe read access to deliverable files for the live preview. When the agent activity log
(activity.py, phase 1) is installed, its current state and recent events are included too,
so the page switches to it without changes.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import memory, paths

AGENTS_DIR = paths.REPO_ROOT / ".claude" / "agents"
SKILLS_DIR = paths.REPO_ROOT / ".claude" / "skills"

# Draft grouping from the T54 plan, section 7 (final grouping still to agree with the owner).
TEAMS = [
    {"id": "research", "name": "Research", "agents": ["product-research", "market-research", "creative-research"],
     "job": "Finds products, reads customers and studies competitor ads."},
    {"id": "creative", "name": "Creative", "agents": ["creative-director"],
     "job": "Turns research into angles, image briefs and ad copy."},
    {"id": "store", "name": "Store", "agents": ["shopify", "store-qa"],
     "job": "Drafts product pages and checks the store like a customer."},
    {"id": "ads", "name": "Ads", "agents": ["meta-ads", "meta-ads-auditor"],
     "job": "Prepares Meta campaigns and audits the ad account."},
    {"id": "testing", "name": "Testing", "agents": ["product-testing"],
     "job": "Runs each product test and calls the verdict."},
    {"id": "money", "name": "Money", "agents": ["accountant"],
     "job": "Tracks every cost and sale against the weekly target."},
    {"id": "learning", "name": "Learning", "agents": ["mentor-learning"],
     "job": "Turns the mentor's lessons into playbooks for every agent."},
    {"id": "operations", "name": "Operations", "agents": ["operations"],
     "job": "Keeps the task list, bottlenecks and daily overview."},
]

# MCP servers and built-in tools, by the name the owner knows them by.
CONNECTORS = {
    "Shopify": "Shopify store (read-only)",
    "claude_ai_Shopify": "Shopify store (read-only)",
    "claude-in-chrome": "Chrome browser",
    "computer-use": "Computer use (Master Key tools)",
}
BUILTIN = {"WebSearch": "Web search", "WebFetch": "Web pages", "Bash": "JARVIS command line",
           "Read": "Files", "Write": "Files", "Edit": "Files", "Glob": "Files", "Grep": "Files"}

PREVIEW_TEXT = {".md", ".txt", ".csv", ".json"}
PREVIEW_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".gif": "image/gif"}
PREVIEW_MAX_TEXT = 6000
PREVIEW_MAX_IMAGE = 8 * 1024 * 1024


def _list(value) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,\s]+", str(value or "").strip("[] "))
    return [str(i).strip().strip("'\"") for i in items if str(i).strip()]


def connections(tools: list[str]) -> list[str]:
    """What an agent is plugged into, in plain words, from its tool list."""
    out = []
    for tool in tools:
        if tool.startswith("mcp__"):
            name = CONNECTORS.get(tool.split("__")[1], tool.split("__")[1].replace("_", " "))
        else:
            name = BUILTIN.get(tool)
        if name and name not in out:
            out.append(name)
    return out


def skills_for(meta: dict, body: str) -> list[str]:
    """Skills the agent preloads, then the playbooks its instructions tell it to load."""
    names = _list(meta.get("skills"))
    installed = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()} if SKILLS_DIR.exists() else set()
    for found in re.findall(r"`?\b((?:blake|jarvis|master)-[a-z0-9-]+)\b`?", body):
        if found not in names and (found in installed or found.startswith("blake-")):
            names.append(found)
    return names


def agents() -> dict[str, dict]:
    out = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        meta, body = memory.parse(path.read_text(encoding="utf-8"))
        name = meta.get("name") or path.stem
        tools = _list(meta.get("tools"))
        out[name] = {"tools": tools, "connections": connections(tools), "skills": skills_for(meta, body),
                     "model": meta.get("model", "inherit")}
    return out


def recent_work(conn, limit: int = 6) -> dict[str, list[dict]]:
    """Each agent's latest finished tasks, newest first."""
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT id, title, agent, product, deliverable, summary, completed FROM tasks"
                          " WHERE status = 'done' AND agent IS NOT NULL ORDER BY completed DESC LIMIT 300"):
        rows = out.setdefault(r["agent"], [])
        if len(rows) < limit:
            rows.append(dict(r))
    return out


def live_activity(conn) -> dict | None:
    """The phase 1 activity log, when it is installed; None until then."""
    try:
        from . import activity
    except ImportError:
        return None
    try:
        return {"agents": activity.current(conn), "events": activity.recent(conn, 60)}
    except Exception:  # table not created yet on this brain
        return None


def data(conn) -> dict:
    return {"teams": TEAMS, "agents": agents(), "recent": recent_work(conn), "activity": live_activity(conn)}


def resolve(ref: str) -> Path | None:
    """A deliverable path (as tasks and events store it) inside the brain, or None if it isn't one."""
    ref = (ref or "").strip().replace("\\", "/")
    if not ref:
        return None
    home = paths.home()
    candidate = Path(ref)
    if not candidate.is_absolute():  # tasks store "brain/work/...": the brain may live elsewhere (JARVIS_HOME)
        candidate = home / (ref[len("brain/"):] if ref.startswith("brain/") else ref)
    try:
        candidate = candidate.resolve()
        candidate.relative_to(home)
    except (OSError, ValueError):
        return None
    if candidate.name.startswith(".") or "secrets" in candidate.relative_to(home).parts:
        return None
    return candidate if candidate.is_file() else None


def preview(ref: str) -> tuple[bytes, str] | None:
    """(body, content type) for a preview, or None when the file isn't there or can't be shown."""
    path = resolve(ref)
    if not path:
        return None
    ext = path.suffix.lower()
    if ext in PREVIEW_IMAGE:
        if path.stat().st_size > PREVIEW_MAX_IMAGE:
            return None
        return path.read_bytes(), PREVIEW_IMAGE[ext]
    if ext in PREVIEW_TEXT:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return fh.read(PREVIEW_MAX_TEXT).encode("utf-8"), "text/plain; charset=utf-8"
    return None
