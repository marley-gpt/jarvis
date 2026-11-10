"""The agent roster and the small controls round it: each agent's tools, connectors, skills and model from
`.claude/agents/*.md`, model choice per agent and per task, a new task from the page, connector health as Claude
Code reports it, the consequential actions log, the saved store and ad figures (`bin/jarvis metrics`), the daily
profit line, and safe read access to deliverable files for previews. (This was the data half of the 3D HQ, which
was removed in T110; the page-independent parts live on here.)
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
# JARVIS's recommendation per agent (the owner can still pick anything): the strongest model where the work is
# creative or customer-facing, a mid model for research and analysis, the cheapest for record-keeping.
RECOMMENDED = {
    "creative-director": ("opus", "ad concepts and copy: the strongest model earns its cost"),
    "shopify": ("opus", "product page builds and CRO: wording and layout decide sales"),
    "store-qa": ("sonnet", "clicking through the store and writing fixes: careful, not creative"),
    "market-research": ("sonnet", "reading reviews and threads at volume: good judgement, lower cost"),
    "product-research": ("sonnet", "dossiers from many sources: steady analysis"),
    "creative-research": ("sonnet", "ad library and competitor analysis"),
    "meta-ads": ("sonnet", "campaign drafts and performance reads"),
    "meta-ads-auditor": ("sonnet", "graded audits against a checklist"),
    "mentor-learning": ("sonnet", "turning lessons into cited notes: accuracy over flair"),
    "product-testing": ("haiku", "test records, metrics and verdicts: structured work"),
    "operations": ("haiku", "status reports and priorities: fast and cheap"),
    "accountant": ("haiku", "the ledger: exact, simple, frequent"),
}


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
    """A new task from the page: goes on the backlog JARVIS works from, assigned to an agent if one was picked."""
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
    for r in conn.execute("SELECT ts, tool, summary FROM actions ORDER BY id DESC LIMIT ?", (limit * 60,)):
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
