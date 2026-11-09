"""The Brain page (T95): the knowledge graph of memory, agents, products, tasks and decisions, and what JARVIS is doing.

Read-only over the brain. `graph()` builds nodes and edges from the database in a few queries and is cached on
the brain's fingerprint, so a page polling it with 1200+ entries costs nothing until something changes. `now()`
is the live panel: running workers and agents, open tasks, pending approvals and the latest journal lines.

Edges: [[links]] and record ids written in a memory body (M-…, T<n>, D<n>), supersedes, task parent and agent,
product tags on memory, tasks and decisions, each approval to its task, each brain file (a task's deliverable, the
source of a memory entry) to what made it, and each memory to its category hub. Agents and category hubs hang
off the JARVIS node at the centre, where the page draws the orb.
"""

from __future__ import annotations

import re
import threading
import time

from . import activity, journal, records, work

MEMORY_ID = re.compile(r"\bM-\d{6}-[0-9a-f]{4}\b")
WIKI_LINK = re.compile(r"\[\[([^\]\n|]{1,200})(?:\|[^\]\n]*)?\]\]")
TASK_REF = re.compile(r"\bT(\d{1,5})\b")
DECISION_REF = re.compile(r"\bD(\d{1,5})\b")
WORKER_TTL = 10  # seconds; listing workers asks the OS whether each running one is still alive

_cache: dict = {}
_lock = threading.Lock()


def fingerprint(conn) -> tuple:
    """Changes whenever a node or edge could have changed."""
    parts = [conn.execute(f"SELECT COUNT(*), MAX(rowid) FROM {t}").fetchone()[:]
             for t in ("memory", "tasks", "decisions", "products", "approvals")]
    parts += [conn.execute(f"SELECT MAX(updated) FROM {t}").fetchone()[0] for t in ("memory", "tasks", "products")]
    parts.append(conn.execute("SELECT MAX(decided_ts) FROM approvals").fetchone()[0])
    return tuple(parts)


def _label(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def graph(conn, agents: list[str]) -> dict:
    sig = fingerprint(conn)
    key = (str(conn.execute("PRAGMA database_list").fetchone()[2]), sig, tuple(agents))
    with _lock:
        if _cache.get("key") == key:
            return _cache["data"]
    data = _build(conn, agents)
    data["sig"] = repr(sig)
    with _lock:
        _cache.update(key=key, data=data)
    return data


def _build(conn, agents: list[str]) -> dict:
    nodes: list[dict] = []
    index: dict[str, int] = {}
    links: list[list] = []
    seen: set[tuple] = set()

    def node(nid: str, kind: str, group: str, label: str, **extra) -> int:
        index[nid] = len(nodes)
        nodes.append({"id": nid, "k": kind, "g": group, "l": label, **extra})
        return index[nid]

    def link(a: str | None, b: str | None, kind: str) -> None:
        if not a or not b or a == b or a not in index or b not in index:
            return
        ia, ib = index[a], index[b]
        pair = (min(ia, ib), max(ia, ib))
        if pair not in seen:
            seen.add(pair)
            links.append([ia, ib, kind])

    node("jarvis", "jarvis", "jarvis", "JARVIS")
    for name in agents:
        node(f"agent:{name}", "agent", "agent", name)
        link("jarvis", f"agent:{name}", "team")

    for p in conn.execute("SELECT slug, name, stage FROM products"):
        node(f"product:{p['slug']}", "product", "product", p["name"], s=p["stage"])
        link("jarvis", f"product:{p['slug']}", "product")

    mem = conn.execute("SELECT id, title, category, product, status, supersedes, provenance FROM memory").fetchall()
    titles = {}
    for m in mem:
        if f"cat:{m['category']}" not in index:
            node(f"cat:{m['category']}", "category", m["category"], m["category"].replace("-", " "))
            link("jarvis", f"cat:{m['category']}", "hub")
        node(m["id"], "memory", m["category"], _label(m["title"]), s=m["status"], p=m["provenance"])
        titles[m["title"].strip().lower()] = m["id"]

    tasks = conn.execute("SELECT id, parent_id, title, agent, product, status FROM tasks").fetchall()
    for t in tasks:
        node(f"T{t['id']}", "task", "task", _label(f"T{t['id']} {t['title']}"), s=t["status"])
    decisions = conn.execute("SELECT id, product, task_id, decision FROM decisions").fetchall()
    for d in decisions:
        node(f"D{d['id']}", "decision", "decision", _label(f"D{d['id']} {d['decision']}"))

    # Structural edges first, so a body reference never takes the place of one.
    for m in mem:
        link(m["id"], f"cat:{m['category']}", "in")
        link(m["id"], m["supersedes"], "supersedes")
        if m["product"]:
            link(m["id"], f"product:{m['product']}", "product")
    for t in tasks:
        tid = f"T{t['id']}"
        link(tid, f"T{t['parent_id']}" if t["parent_id"] else None, "parent")
        link(tid, f"agent:{t['agent']}" if t["agent"] else None, "agent")
        link(tid, f"product:{t['product']}" if t["product"] else None, "product")
    for d in decisions:
        did = f"D{d['id']}"
        link(did, f"T{d['task_id']}" if d["task_id"] else None, "task")
        link(did, f"product:{d['product']}" if d["product"] else None, "product")
    # Approvals hang off their task; files are the deliverables of tasks and the brain files memory was taken from,
    # so a hint like [[show A3]] or [[file brain/work/x.md]] has a node to light up.
    for a in conn.execute("SELECT id, task_id, kind, summary, status FROM approvals"):
        node(f"A{a['id']}", "approval", "approval", _label(f"A{a['id']} {a['summary']}"), s=a["status"])
        link(f"A{a['id']}", f"T{a['task_id']}" if a["task_id"] else None, "task")
    owners = [(f"T{t['id']}", t["deliverable"]) for t in conn.execute("SELECT id, deliverable FROM tasks")]
    owners += [(m["id"], m["source_ref"]) for m in conn.execute("SELECT id, source_ref FROM memory")]
    for owner, path in owners:
        path = (path or "").strip()
        if path.startswith("brain/") and owner in index:
            fid = f"file:{path}"
            if fid not in index:
                node(fid, "file", "file", path.rsplit("/", 1)[-1])
            link(fid, owner, "file")
    _attach(nodes, links, seen)

    for row in conn.execute("SELECT id, body FROM memory_fts"):
        mid, body = row["id"], row["body"] or ""
        if mid not in index:
            continue
        for target in WIKI_LINK.findall(body):
            target = target.strip()
            link(mid, target if target in index else titles.get(target.lower()), "link")
        for target in set(MEMORY_ID.findall(body)):
            link(mid, target, "ref")
        for n in set(TASK_REF.findall(body)):
            link(mid, f"T{int(n)}", "ref")
        for n in set(DECISION_REF.findall(body)):
            link(mid, f"D{int(n)}", "ref")

    counts: dict[str, int] = {}
    for n in nodes:
        k = n["g"] if n["k"] in ("memory", "category") else n["k"]
        if n["k"] != "category":
            counts[k] = counts.get(k, 0) + 1
    return {"nodes": nodes, "links": links, "counts": counts}


def _attach(nodes: list[dict], links: list[list], seen: set[tuple]) -> None:
    """Hang every group of nodes that can't reach JARVIS (a task chain for an agent outside the roster, a decision
    on its own) off the JARVIS node by its best-connected member, so it sits with the rest instead of drifting off
    the edge of the layout. Body references added later only add to this."""
    parent = list(range(len(nodes)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    degree = [0] * len(nodes)
    for a, b, _ in links:
        parent[root(a)] = root(b)
        degree[a] += 1
        degree[b] += 1
    best: dict[int, int] = {}
    for i in range(1, len(nodes)):
        r = root(i)
        if r != root(0) and (r not in best or degree[i] > degree[best[r]]):
            best[r] = i
    for i in best.values():
        seen.add((0, i))
        links.append([0, i, nodes[i]["k"]])


def detail(conn, nid: str, agents: dict[str, dict]) -> dict | None:
    """Everything the side panel shows for one node; None if there is no such node."""
    from . import memory
    if MEMORY_ID.fullmatch(nid):
        try:
            entry = memory.get(conn, nid)
        except memory.MemoryError:
            return None
        return {"kind": "memory", "id": nid, "title": entry.meta.get("title"), "meta": entry.meta, "body": entry.body}
    if re.fullmatch(r"T\d+", nid):
        t = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(nid[1:]),)).fetchone()
        if not t:
            return None
        subs = conn.execute("SELECT id, title, agent, status FROM tasks WHERE parent_id = ? ORDER BY id",
                            (t["id"],)).fetchall()
        events = conn.execute("SELECT ts, kind, note FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 12",
                              (t["id"],)).fetchall()
        return {"kind": "task", "id": nid, "title": t["title"], "row": dict(t), "subtasks": [dict(s) for s in subs],
                "events": [dict(e) for e in events]}
    if re.fullmatch(r"D\d+", nid):
        d = conn.execute("SELECT * FROM decisions WHERE id = ?", (int(nid[1:]),)).fetchone()
        return {"kind": "decision", "id": nid, "title": d["decision"], "row": dict(d)} if d else None
    if re.fullmatch(r"A\d+", nid):
        a = conn.execute("SELECT * FROM approvals WHERE id = ?", (int(nid[1:]),)).fetchone()
        return {"kind": "approval", "id": nid, "title": a["summary"], "row": dict(a)} if a else None
    kind, _, name = nid.partition(":")
    if kind == "file" and name.startswith("brain/"):
        tasks = conn.execute("SELECT id, title, agent, status FROM tasks WHERE deliverable = ? ORDER BY id",
                             (name,)).fetchall()
        mem = conn.execute("SELECT id, title, updated FROM memory WHERE source_ref = ? ORDER BY updated DESC",
                           (name,)).fetchall()
        if not tasks and not mem:
            return None
        return {"kind": "file", "id": nid, "title": name.rsplit("/", 1)[-1], "path": name,
                "tasks": [dict(t) for t in tasks], "recent": [dict(m) for m in mem]}
    if kind == "product":
        p = conn.execute("SELECT * FROM products WHERE slug = ?", (name,)).fetchone()
        if not p:
            return None
        tasks = conn.execute("SELECT id, title, agent, status FROM tasks WHERE product = ? ORDER BY id DESC LIMIT 20",
                             (name,)).fetchall()
        n_mem = conn.execute("SELECT COUNT(*) FROM memory WHERE product = ?", (name,)).fetchone()[0]
        return {"kind": "product", "id": nid, "title": p["name"], "row": dict(p), "tasks": [dict(t) for t in tasks],
                "memory": n_mem}
    if kind == "agent" and name in agents:
        tasks = records.task_list(conn, agent=name, open_only=True, limit=20)
        done = conn.execute("SELECT COUNT(*) FROM tasks WHERE agent = ? AND status = 'done'", (name,)).fetchone()[0]
        return {"kind": "agent", "id": nid, "title": agents[name].get("label", name), "row": agents[name],
                "tasks": [dict(t) for t in tasks], "done": done}
    if kind == "cat":
        n = conn.execute("SELECT COUNT(*) FROM memory WHERE category = ?", (name,)).fetchone()[0]
        if not n:
            return None
        recent = conn.execute("SELECT id, title, updated FROM memory WHERE category = ? AND status = 'active'"
                              " ORDER BY updated DESC LIMIT 12", (name,)).fetchall()
        return {"kind": "category", "id": nid, "title": name, "count": n, "recent": [dict(r) for r in recent]}
    if nid == "jarvis":
        return {"kind": "jarvis", "id": nid, "title": "JARVIS"}
    return None


def _workers() -> list[dict]:
    with _lock:
        hit = _cache.get("workers")
        if hit and time.time() - hit[0] < WORKER_TTL:
            return hit[1]
    try:
        runs = work.listing(12)
    except (OSError, ValueError):
        runs = []
    keep = [{"id": f"W{r['id']}", "task": r.get("task") or "", "agent": r.get("agent") or "",
             "status": r.get("status"), "started": r.get("started"), "brief": _label(r.get("brief", ""), 140)}
            for r in runs]
    with _lock:
        _cache["workers"] = (time.time(), keep)
    return keep


def now(conn, live: dict | None = None) -> dict:
    """The live "what I am doing" panel, small enough to poll every few seconds."""
    live = live or {}
    workers = _workers()
    titles = {}
    for w in workers:
        if w["task"]:
            m = re.fullmatch(r"T?(\d+)", w["task"].strip(), re.I)
            row = conn.execute("SELECT title FROM tasks WHERE id = ?", (int(m.group(1)),)).fetchone() if m else None
            titles[w["id"]] = row["title"] if row else None
            w["task"] = f"T{m.group(1)}" if m else w["task"]
    for w in workers:
        w["title"] = titles.get(w["id"])

    agents = []
    for name, ev in activity.current(conn).items():
        if ev["state"] in activity.ACTIVE or ev["state"] == "assigned":
            agents.append({"agent": name, "state": ev["state"], "action": ev.get("summary") or ev.get("action"),
                           "task": f"T{ev['task_id']}" if ev.get("task_id") else None, "ts": ev["ts"]})
    for name, info in (live.get("agents") or {}).items():
        if not any(a["agent"] == name for a in agents):
            agents.append({"agent": name, "state": "working", "action": info.get("description"), "task": None,
                           "ts": None})

    open_tasks = conn.execute(
        f"""SELECT id, parent_id, title, agent, product, status, updated FROM tasks
            WHERE status IN ({','.join('?' * len(records.OPEN_TASK_STATUSES))})
            ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'waiting_approval' THEN 1 WHEN 'blocked' THEN 2
            ELSE 3 END, updated DESC LIMIT 30""", records.OPEN_TASK_STATUSES).fetchall()
    lines = [{"ts": j["ts"], "role": j["role"], "text": _label(j["text"], 220)} for j in journal.recent(conn, limit=12)]
    approvals = conn.execute("SELECT id, kind, summary FROM approvals WHERE status = 'pending' ORDER BY id").fetchall()
    running = [w for w in workers if w["status"] == "running"]
    busy_agents = [a for a in agents if a["state"] in activity.ACTIVE]
    status = live.get("status") or "idle"
    return {
        "busy": bool(running or busy_agents or status != "idle"),
        "jarvis": status,
        "request": _label(live.get("request") or "", 160) or None,
        "workers": workers,
        "agents": agents,
        "tasks": [dict(t) for t in open_tasks],
        "approvals": [dict(a) for a in approvals],
        "journal": lines,
        "sig": repr(fingerprint(conn)),
        "ts": time.time(),
    }
