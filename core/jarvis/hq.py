"""Data for the 3D HQ page (`/hq`): the teams, what each agent is connected to, and its work.

The live state (statuses, tasks, approvals) still comes from `/api/state` and the event
stream; this adds what that doesn't carry: the eight teams of T54 (plan section 7), each
agent's tools, connectors and skills from `.claude/agents/*.md`, its recent finished work,
and safe read access to deliverable files for the live preview. When the agent activity log
(activity.py, phase 1) is installed, its current state and recent events are included too,
so the page switches to it without changes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import memory, paths, records

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

# Model choice (plan: every agent on the Claude model best suited to its task). An agent's default lives in its
# file's `model:` line, which Claude Code reads when JARVIS delegates; a task can override it, and the override is
# shown in JARVIS's session brief so it is passed to the Agent tool for that task.
MODELS = ("inherit", "opus", "sonnet", "haiku", "fable")
MODEL_IDS = {"fable": "claude-fable-5-1"}   # Claude Code knows opus/sonnet/haiku by name; Fable goes in by id
MODEL_LINE = re.compile(r"^model:[^\n]*$", re.M)


def model_alias(value: str | None) -> str:
    """The short name for what an agent file says: 'claude-fable-5-1' -> 'fable', missing -> 'inherit'."""
    v = (value or "inherit").strip().lower()
    for alias, full in MODEL_IDS.items():
        if v == full or v.startswith(alias):
            return alias
    for alias in MODELS:
        if alias in v:
            return alias
    return "inherit"


def set_agent_model(name: str, model: str) -> str:
    """Change the `model:` line in .claude/agents/<name>.md. Returns the value written."""
    if model not in MODELS:
        raise ValueError(f"Model must be one of: {', '.join(MODELS)}")
    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        raise ValueError("Bad agent name.")
    path = AGENTS_DIR / f"{name}.md"
    if not path.is_file():
        raise ValueError(f"No agent file for '{name}'.")
    text = path.read_text(encoding="utf-8", newline="")   # keep the file's own line endings
    if not text.startswith("---"):
        raise ValueError(f"{path.name} has no frontmatter.")
    value = MODEL_IDS.get(model, model)
    head, sep, body = text[3:].partition("\n---")
    if MODEL_LINE.search(head):
        head = MODEL_LINE.sub(f"model: {value}", head, count=1)
    else:
        head = head.rstrip("\n") + f"\nmodel: {value}\n"
    path.write_text("---" + head + sep + body, encoding="utf-8", newline="")
    return value


def _agent_voices() -> dict:
    from . import tts
    return tts.agent_voices()


def task_models() -> dict:
    tm = paths.config().get("task_models") or {}
    return tm if isinstance(tm, dict) else {}


def set_task_model(task_id, model: str | None) -> dict:
    """A model for one task (T12: opus), kept in brain/config.json; '' or inherit clears it."""
    key = f"T{records.parse_task_id(task_id)}"
    model = (model or "inherit").strip().lower()
    if model not in MODELS:
        raise ValueError(f"Model must be one of: {', '.join(MODELS)}")
    tm = task_models()
    if model == "inherit":
        tm.pop(key, None)
    else:
        tm[key] = model
    paths.set_config("task_models", tm)
    return tm


def add_task(conn, title: str, *, agent=None, product=None, description=None, model=None) -> int:
    """A new task from HQ: goes on the backlog JARVIS works from, assigned to an agent if one was picked."""
    title = " ".join((title or "").split())
    if not title:
        raise ValueError("A task needs a title.")
    if agent and agent not in agents():
        raise ValueError(f"No agent called '{agent}'.")
    tid = records.task_add(conn, title[:300], agent=agent or None, product=product or None, description=description or None)
    if model and model != "inherit":
        set_task_model(tid, model)
    return tid


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
                     "model": model_alias(meta.get("model"))}
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


def board(conn) -> dict:
    """Every open task, and what was finished in the last two weeks: the Now / Needs you / Done / To do board."""
    marks = ",".join("?" * len(records.OPEN_TASK_STATUSES))
    open_rows = conn.execute(f"SELECT id, parent_id, title, description, agent, product, status, priority, deliverable,"
                             f" summary, created, updated FROM tasks WHERE status IN ({marks}) ORDER BY priority, id DESC",
                             records.OPEN_TASK_STATUSES).fetchall()
    since = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    done = conn.execute("SELECT id, parent_id, title, description, agent, product, status, deliverable, summary, created,"
                        " updated, completed FROM tasks WHERE status = 'done' AND completed >= ? ORDER BY completed DESC"
                        " LIMIT 40", (since,)).fetchall()
    return {"open": [dict(r) for r in open_rows], "done": [dict(r) for r in done]}


def week(conn) -> dict:
    """This week so far (Monday to today) against the weekly profit target."""
    today = datetime.now()
    start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    rep = records.money_report(conn, start, today.strftime("%Y-%m-%d"))
    return {"start": start, "profit": rep["profit"], "income": rep["total_income"], "expenses": rep["total_expenses"],
            "target": paths.config()["weekly_profit_target_aud"], "pace": rep["target"], "days": rep["days"]}


def daily_profit(conn, days: int = 14) -> list[dict]:
    """Profit per day from the accountant's ledger (sales less costs), oldest first; days with nothing logged are 0."""
    start = (datetime.now() - timedelta(days=days - 1)).date()
    rows = {r["day"]: r for r in conn.execute(
        "SELECT day, SUM(CASE WHEN kind = 'income' THEN amount_aud ELSE 0 END) AS income,"
        " SUM(CASE WHEN kind = 'expense' THEN amount_aud ELSE 0 END) AS expenses FROM money WHERE day >= ? GROUP BY day",
        (start.isoformat(),))}
    out = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        r = rows.get(day)
        income, expenses = (r["income"] or 0, r["expenses"] or 0) if r else (0, 0)
        out.append({"day": day, "income": round(income, 2), "expenses": round(expenses, 2), "profit": round(income - expenses, 2)})
    return out


# Connector status, as Claude Code itself reports it (`claude mcp list`): the same connectors the desktop app and
# the dashboard's JARVIS conversation use. Checked only when asked (it starts Claude Code), and kept in memory.
_mcp: dict = {"checked": None, "servers": None, "error": None, "running": False}
_mcp_lock = threading.Lock()
MCP_LINE = re.compile(r"^(?P<name>.+?):\s+(?P<target>\S.*?)\s+-\s+(?P<status>.+)$")  # "name: url - ✔ Connected"


def _claude() -> list[str] | None:
    cfg = paths.config().get("claude_command")
    if cfg:
        return cfg if isinstance(cfg, list) else [cfg]
    exe = shutil.which("claude")
    native = Path.home() / ".local" / "bin" / "claude.exe"
    return [exe] if exe else [str(native)] if native.exists() else None


def _check_connectors() -> None:
    try:
        claude = _claude()
        if not claude:
            raise FileNotFoundError("Claude Code isn't installed on this computer.")
        res = subprocess.run([*claude, "mcp", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=120, cwd=paths.REPO_ROOT, stdin=subprocess.DEVNULL, creationflags=paths.NO_WINDOW)
        servers = []
        for line in res.stdout.splitlines():
            m = MCP_LINE.match(line.strip())
            if m:
                status = m["status"].strip()
                ok = "connected" in status.lower() and "fail" not in status.lower()
                servers.append({"name": m["name"].strip(), "status": re.sub(r"^\W+", "", status), "ok": ok})
        _mcp.update(servers=servers, error=None if servers or res.returncode == 0 else (res.stderr.strip() or "no answer")[:300])
    except (OSError, subprocess.SubprocessError) as exc:
        _mcp.update(error=str(exc)[:300])
    finally:
        _mcp.update(checked=paths.now(), running=False)


def check_connectors() -> dict:
    """Start a check unless one is running; returns the state so far."""
    with _mcp_lock:
        if not _mcp["running"]:
            _mcp["running"] = True
            threading.Thread(target=_check_connectors, daemon=True).start()
    return connectors()


def connectors() -> dict:
    return {k: _mcp[k] for k in ("checked", "servers", "error", "running")}


ACTION_FIELDS = ("title", "handle", "name", "id", "productId", "product_id", "query")


def recent_actions(conn, limit: int = 12) -> list[dict]:
    """Consequential connector calls JARVIS made (the hooks' action log: store edits, ads, files), newest first,
    each with a short readable line and, for Shopify objects, a link into the admin when `shopify_admin_url` is set."""
    from . import hooks
    admin = (paths.config().get("shopify_admin_url") or "").rstrip("/")
    out = []
    for r in conn.execute("SELECT ts, tool, summary FROM actions ORDER BY id DESC LIMIT ?", (limit * 4,)):
        tool, summary = r["tool"], r["summary"] or ""
        server = tool.split("__")[1] if tool.count("__") >= 2 else tool
        if hooks.LOCAL_MCP_SERVERS.match(server) or len(out) >= limit:
            continue   # the app's own tools (sessions, browser, terminal) are not store, ad or file changes
        what = ""
        try:
            data = json.loads(summary.rstrip("…")) if summary.startswith("{") else {}
        except ValueError:
            data = {}
        for key in ACTION_FIELDS:
            if isinstance(data.get(key), (str, int)):
                what = str(data[key])[:90]
                break
        link = None
        m = re.search(r"gid://shopify/(Product|Collection|Page)/(\d+)", summary)
        if m and admin:
            link = f"{admin}/{m.group(1).lower()}s/{m.group(2)}"
        out.append({"ts": r["ts"], "tool": tool.split("__")[-1].replace("_", " ").replace("-", " "),
                    "service": hooks.service_name(tool, server), "what": what, "link": link})
    return out


# Live store and ad figures (plan section 14). The dashboard server has no connectors of its own: JARVIS pulls them
# read-only through its Claude Code session (Shopify and Meta ads connectors) and saves a snapshot with
# `bin/jarvis metrics set`. The page shows the snapshot with its source and age, or says it hasn't been pulled.
METRIC_KEYS = {
    "shopify": ("sessions", "orders", "revenue", "aov", "cvr", "refunds", "add_to_cart", "checkouts"),
    "meta": ("spend", "impressions", "cpm", "clicks", "ctr", "cpc", "add_to_cart", "checkouts", "purchases", "cpa", "roas"),
}


def metrics_path() -> Path:
    return paths.home() / "metrics" / "latest.json"


def metrics() -> dict | None:
    try:
        snap = json.loads(metrics_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) and snap.get("pulled") else None


def metrics_set(snapshot: dict) -> dict:
    """Save a snapshot {period, shopify: {...}, meta: {...}, products: [...], notes}; stamps `pulled` now."""
    if not isinstance(snapshot, dict):
        raise ValueError("The snapshot must be a JSON object.")
    clean = {"pulled": paths.now(), "period": str(snapshot.get("period") or "today"), "source": str(snapshot.get("source") or "Shopify and Meta ads connectors, read-only"),
             "notes": str(snapshot.get("notes") or "")}
    for section, keys in METRIC_KEYS.items():
        vals = snapshot.get(section) or {}
        if not isinstance(vals, dict):
            raise ValueError(f"'{section}' must be an object of figures.")
        clean[section] = {k: vals[k] for k in keys if isinstance(vals.get(k), (int, float))}
    products = snapshot.get("products") or []
    clean["products"] = [{"name": str(p.get("name", "")), **{k: p[k] for k in ("spend", "revenue", "orders", "roas", "cpa") if isinstance(p.get(k), (int, float))}}
                         for p in products if isinstance(p, dict)][:20]
    path = metrics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


def data(conn) -> dict:
    cfg = paths.config()
    return {"teams": TEAMS, "agents": agents(), "recent": recent_work(conn), "activity": live_activity(conn),
            "board": board(conn), "week": week(conn), "daily": daily_profit(conn), "connectors": connectors(),
            "actions": recent_actions(conn), "metrics": metrics(), "models": MODELS, "task_models": task_models(),
            "agent_voices": _agent_voices(),
            "products": [r["slug"] for r in conn.execute("SELECT slug FROM products ORDER BY slug")],
            # Set by JARVIS or the owner in brain/config.json; the page says so when they're missing.
            "milestone": cfg.get("hq_milestone"), "allowance": cfg.get("max_allowance"),
            # Plan section 16 (parked): unlocks earned on real strong sales days. Nothing is unlocked until the rules are agreed.
            "rewards": rewards(conn)}


def rewards(conn) -> list[str]:
    """Hook for plan section 16: reward ids unlocked by real sales. Empty until the owner agrees the rules."""
    return []


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
