"""Agents at work (T106): every agent working right now, from what they really did.

The owner wants to watch the team work (M-261109-dea4, the reel in M-261109-dbbe): who is live, what each one is
doing, what it last made and how far along it is. Nothing here is invented. Each action comes from a durable
worker's stream-json log (brain/work/runs/W<n>.log, read incrementally as it grows) or from the dashboard session's
own hand-offs (`note`, called by the Runner). When nothing runs the view says so.

    the `live` section of the app's state (cockpit.live_view): agents, the action feed, counts
    GET /api/swarm/file     an image an agent made, for the preview (repo files only, never secrets)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import paths, work

RECENT_SECONDS = 15 * 60     # a finished worker stays on the board this long, dimmed
WINDOW_SECONDS = 30 * 60     # the timeline's width
FEED_MAX = 120
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
IMAGE_MAX = 8 * 1024 * 1024
WORKER_CACHE_SECONDS = 4     # work.listing asks the OS about each running worker

_lock = threading.Lock()
_logs: dict[str, dict] = {}            # log path -> parse state
_workers_cache: dict = {}
_session: deque = deque(maxlen=200)    # (ts, agent, text, kind) from the dashboard session's hand-offs


# ---------------------------------------------------------------- plain English for a tool call

def _base(p: str) -> str:
    return Path(str(p).replace("\\", "/")).name or str(p)


def _clip(s: str, n: int = 90) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _command(cmd: str) -> str:
    """The part of a shell line worth showing: the first step that isn't a `cd` or a variable being set."""
    for part in re.split(r"&&|\|\||;|\n", cmd):
        part = part.strip()
        if not part or part.startswith(("cd ", "Set-Location", "#")) or re.match(r"^\$?[A-Za-z_]\w*=", part):
            continue
        return part
    return cmd.strip()


def plain(name: str, data: dict) -> str:
    """One tool call as the owner would say it: "Editing app.js", "Searching the web for …"."""
    data = data if isinstance(data, dict) else {}
    if name == "Bash" or name == "PowerShell":
        if data.get("description"):
            return f"Running: {_clip(data['description'], 80)}"
        what = _command(str(data.get("command") or ""))
        return f"Running {_clip(what, 70)}" if what else "Running a command"
    if name == "Read":
        return f"Reading {_base(data.get('file_path', ''))}"
    if name == "Write":
        return f"Writing {_base(data.get('file_path', ''))}"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Editing {_base(data.get('file_path') or data.get('notebook_path', ''))}"
    if name == "Grep":
        return f"Searching files for “{_clip(data.get('pattern', ''), 50)}”"
    if name == "Glob":
        return f"Looking for {_clip(data.get('pattern', ''), 50)}"
    if name == "WebSearch":
        return f"Searching the web for “{_clip(data.get('query', ''), 60)}”"
    if name == "WebFetch":
        url = str(data.get("url", ""))
        host = url.split("//", 1)[-1].split("/", 1)[0]
        return f"Reading a web page on {host}" if host else "Reading a web page"
    if name in ("Agent", "Task"):
        return f"Handing “{_clip(data.get('description', ''), 50)}” to {data.get('subagent_type') or 'a helper'}"
    if name == "Skill":
        return f"Loading the {data.get('skill', '')} playbook"
    if name == "TodoWrite":
        return "Updating its plan"
    if name == "ToolSearch":
        return "Looking up a tool"
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1].replace("claude_ai_", "").replace("_", " ") if len(parts) > 1 else "a connector"
        action = parts[-1].replace("_", " ").replace("-", " ") if len(parts) > 2 else ""
        return f"Using {server}: {action}".rstrip(": ")
    return f"Using {name}"


def _made(name: str, data: dict) -> dict | None:
    """What a tool call produced, for the preview: a file written or edited (with a text snippet from the call
    itself), or None."""
    if name not in ("Write", "Edit", "MultiEdit", "NotebookEdit") or not isinstance(data, dict):
        return None
    path = str(data.get("file_path") or data.get("notebook_path") or "")
    if not path:
        return None
    ext = Path(path).suffix.lower()
    try:
        shown = Path(path).relative_to(paths.REPO_ROOT).as_posix()
    except ValueError:  # outside the repo (a scratch folder): the folder and the file are enough
        shown = "/".join(Path(path).parts[-2:]).replace("\\", "/")
    if ext in IMAGE_EXT:
        return {"path": path, "shown": shown, "kind": "image"}
    text = data.get("content") if name == "Write" else data.get("new_string") or data.get("new_source")
    if name == "MultiEdit" and isinstance(data.get("edits"), list) and data["edits"]:
        text = (data["edits"][-1] or {}).get("new_string")
    return {"path": path, "shown": shown, "kind": "text", "snippet": str(text or "")[-900:], "verb": "wrote" if name == "Write" else "edited"}


def _ts(ev: dict, fallback: float) -> float:
    raw = ev.get("timestamp")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


# ---------------------------------------------------------------- a worker's log, read as it grows

def _fresh(path: str) -> dict:
    return {"offset": 0, "rest": b"", "actions": [], "made": None, "todos": None, "subs": {}, "last": None,
            "words": None, "finished": None}


def _apply(st: dict, ev: dict, now: float) -> None:
    kind = ev.get("type")
    if kind == "result":
        st["finished"] = {"ok": not ev.get("is_error"), "text": _clip(ev.get("result") or "", 240)}
        return
    msg = ev.get("message") if isinstance(ev.get("message"), dict) else None
    content = msg.get("content") if msg else None
    if not isinstance(content, list):
        return
    t = _ts(ev, now)
    parent = ev.get("parent_tool_use_id")
    who = st["subs"].get(parent, {}).get("agent") if parent else None
    if kind == "user":
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in st["subs"]:
                st["subs"][b["tool_use_id"]]["done"] = t
        return
    if kind != "assistant":
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and b.get("text", "").strip() and not parent:
            st["words"] = {"t": t, "text": _clip(b["text"], 220)}
        if b.get("type") != "tool_use":
            continue
        name, data = b.get("name", ""), b.get("input") or {}
        act = {"t": t, "text": plain(name, data), "tool": name, "sub": who}
        st["actions"].append(act)
        st["last"] = act
        if name in ("Agent", "Task"):
            st["subs"][b.get("id")] = {"agent": data.get("subagent_type") or "helper",
                                       "what": _clip(data.get("description", ""), 60), "t": t, "done": None}
        if name == "TodoWrite" and isinstance(data.get("todos"), list):
            todos = [x for x in data["todos"] if isinstance(x, dict)]
            st["todos"] = {"done": sum(1 for x in todos if x.get("status") == "completed"), "total": len(todos),
                           "now": next((x.get("activeForm") or x.get("content") for x in todos
                                        if x.get("status") == "in_progress"), None)}
        made = _made(name, data)
        if made:
            st["made"] = {**made, "t": t}
    del st["actions"][:-400]


def read_log(path: str) -> dict:
    """The parse state of one worker log, brought up to date with whatever was appended since the last call."""
    now = time.time()
    with _lock:
        st = _logs.setdefault(path, _fresh(path))
        p = Path(path)
        try:
            size = p.stat().st_size
        except OSError:
            return st
        if size < st["offset"]:  # the log was replaced: start over
            st.clear()
            st.update(_fresh(path))
        if size == st["offset"]:
            return st
        with p.open("rb") as fh:
            fh.seek(st["offset"])
            chunk = st["rest"] + fh.read(size - st["offset"])
        st["offset"] = size
        lines = chunk.split(b"\n")
        st["rest"] = lines.pop()  # a line still being written
        for raw in lines:
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue
            try:
                ev = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(ev, dict):
                _apply(st, ev, now)
        return st


# ---------------------------------------------------------------- the dashboard session's own hand-offs

def note(agent: str, text: str, kind: str = "action") -> None:
    """A line from an agent the dashboard session handed work to (the Runner calls this)."""
    _session.append({"t": time.time(), "agent": agent, "text": _clip(text, 140), "kind": kind})


# ---------------------------------------------------------------- the view

def _workers() -> list[dict]:
    hit = _workers_cache.get("rows")
    if hit and time.time() - hit[0] < WORKER_CACHE_SECONDS:
        return hit[1]
    rows = work.listing(40)
    _workers_cache["rows"] = (time.time(), rows)
    return rows


def _iso(t: float | None) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat(timespec="seconds") if t else None


def _parse_started(s: str | None) -> float | None:
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def _task_progress(conn, task: str | None) -> dict | None:
    """Subtasks done of total for the worker's task, when it has any."""
    try:
        tid = int(str(task or "").upper().lstrip("T"))
    except ValueError:
        return None
    row = conn.execute("SELECT title, status FROM tasks WHERE id=?", (tid,)).fetchone() if conn else None
    if not row:
        return None
    subs = conn.execute("SELECT status FROM tasks WHERE parent_id=?", (tid,)).fetchall()
    out = {"task": f"T{tid}", "title": row[0], "status": row[1]}
    if subs:
        out.update(done=sum(1 for s in subs if s[0] in ("done", "cancelled")), total=len(subs))
    return out


def _brief_title(brief: str) -> str:
    text = " ".join((brief or "").split())
    if text.lower().startswith("task t"):  # "Task T106 (repo …). Owner wants …": lead with what it is for
        text = text.split(". ", 1)[-1] if ". " in text[:160] else text
    return _clip(text, 110)


def state(conn=None, live: dict | None = None, workers: list[dict] | None = None) -> dict:
    now = time.time()
    agents, feed = [], []
    for w in (_workers() if workers is None else workers):
        status = w.get("status")
        st = read_log(w["log"]) if w.get("log") else _fresh("")
        last_t = (st["last"] or {}).get("t") or _parse_started(w.get("updated") or w.get("started"))
        running = status == "running"
        if not running and (not last_t or now - last_t > RECENT_SECONDS):
            continue
        key = f"W{w['id']}"
        name = w.get("agent") or "general-purpose"
        made = st["made"]
        if made and made["kind"] == "image":
            made = {**made, "url": "/api/swarm/file?path=" + quote(made["path"])}
        progress = None
        if st["todos"] and st["todos"]["total"]:
            progress = {"source": "plan", **st["todos"]}
        task = _task_progress(conn, w.get("task"))
        if not progress and task and task.get("total"):
            progress = {"source": "subtasks", "done": task["done"], "total": task["total"], "now": None}
        agents.append({
            "key": key, "name": name, "label": f"{name} · {key}", "kind": "worker", "running": running,
            "status": status, "task": w.get("task"), "task_title": (task or {}).get("title"),
            "doing": ((st["todos"] or {}).get("now") or (st["last"] or {}).get("text")) if running
                     else (st["finished"] or {}).get("text") or "Stopped before it finished",
            "title": _brief_title(w.get("brief", "")), "started": w.get("started"),
            "last": _iso(last_t), "actions": len(st["actions"]), "made": made, "progress": progress,
            "words": st["words"], "finished": st["finished"],
            "ticks": [round(a["t"]) for a in st["actions"] if now - a["t"] <= WINDOW_SECONDS],
        })
        for a in st["actions"][-60:]:
            feed.append({"t": a["t"], "agent": key, "who": a["sub"] or name, "text": a["text"]})
        # A worker's own helpers (the Agent tool inside it) get their own badge while they run.
        for sid, sub in st["subs"].items():
            if sub["done"] is None and running:
                agents.append({"key": f"{key}:{sid[-6:]}", "name": sub["agent"], "label": f"{sub['agent']} (for {key})",
                               "kind": "helper", "running": True, "status": "running", "task": w.get("task"),
                               "doing": sub["what"], "title": sub["what"], "started": _iso(sub["t"]),
                               "last": _iso(max([a["t"] for a in st["actions"] if a["sub"] == sub["agent"]] or [sub["t"]])),
                               "actions": sum(1 for a in st["actions"] if a["sub"] == sub["agent"]), "made": None,
                               "progress": None, "words": None, "finished": None,
                               "ticks": [round(a["t"]) for a in st["actions"]
                                         if a["sub"] == sub["agent"] and now - a["t"] <= WINDOW_SECONDS]})
    # Agents the dashboard session has out right now.
    for name, info in ((live or {}).get("agents") or {}).items():
        mine = [s for s in _session if s["agent"] == name]
        agents.append({"key": f"S:{name}", "name": name, "label": f"{name} (this chat)", "kind": "session",
                       "running": True, "status": "running", "task": None,
                       "doing": mine[-1]["text"] if mine else (info or {}).get("description"),
                       "title": (info or {}).get("description") or "", "started": None,
                       "last": _iso(mine[-1]["t"]) if mine else None, "actions": len(mine), "made": None,
                       "progress": None, "words": None, "finished": None,
                       "ticks": [round(s["t"]) for s in mine if now - s["t"] <= WINDOW_SECONDS]})
    for s in _session:
        if now - s["t"] <= RECENT_SECONDS:
            feed.append({"t": s["t"], "agent": f"S:{s['agent']}", "who": s["agent"], "text": s["text"]})
    feed.sort(key=lambda f: f["t"])
    feed = [{**f, "at": _iso(f["t"]), "t": round(f["t"], 3)} for f in feed[-FEED_MAX:]]
    agents.sort(key=lambda a: (not a["running"], a["kind"] == "helper", a["key"]))
    live_n = sum(1 for a in agents if a["running"])
    return {"now": round(now), "window": WINDOW_SECONDS, "live": live_n, "agents": agents, "feed": feed,
            "sig": f"{live_n}:{len(feed)}:{feed[-1]['t'] if feed else 0}"}


def image(ref: str) -> tuple[bytes, str] | None:
    """An image an agent made, for the preview: inside the repo or the brain, an image type, never secrets."""
    ref = (ref or "").strip()
    if not ref:
        return None
    p = Path(ref)
    if not p.is_absolute():
        p = paths.REPO_ROOT / ref
    ext = p.suffix.lower()
    if ext not in IMAGE_EXT:
        return None
    roots = [Path(paths.REPO_ROOT).resolve(), paths.home().resolve()]
    try:
        # Check the path as written (brain/files links out to C:\JarvisMedia), then the file itself.
        absolute = Path(os.path.normpath(p.absolute()))
        if not any(_under(absolute, r) for r in roots):
            return None
        if "secrets" in absolute.parts or p.name.startswith("."):
            return None
        if not absolute.is_file() or absolute.stat().st_size > IMAGE_MAX:
            return None
        return absolute.read_bytes(), IMAGE_EXT[ext]
    except OSError:
        return None


def _under(p: Path, root: Path) -> bool:
    a, r = os.path.normcase(os.path.normpath(p)), os.path.normcase(os.path.normpath(root))
    return a == r or a.startswith(r.rstrip("\\/") + os.sep)
