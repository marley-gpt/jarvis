"""One record by its id, for the app's record drawer: a memory entry (M-…), a task with its subtasks and history
(T<n>), a decision (D<n>), an approval (A<n>), a test (X<n>), a product (`product:<slug>`), an agent
(`agent:<name>`), a memory category (`cat:<name>`) or a brain file that is a deliverable (`file:brain/...`).
"""

from __future__ import annotations

import re

from . import memory, records

MEMORY_ID = re.compile(r"\bM-\d{6}-[0-9a-f]{4}\b")


def detail(conn, nid: str, agents: dict[str, dict]) -> dict | None:
    """Everything the record drawer shows for one id; None if there is no such record."""
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
        subs = conn.execute("SELECT id, title, agent, status, deliverable FROM tasks WHERE parent_id = ? ORDER BY id",
                            (t["id"],)).fetchall()
        events = conn.execute("SELECT ts, kind, note FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 12",
                              (t["id"],)).fetchall()
        return {"kind": "task", "id": nid, "title": t["title"], "row": dict(t), "subtasks": [dict(s) for s in subs],
                "events": [dict(e) for e in events]}
    if re.fullmatch(r"X\d+", nid):
        x = conn.execute("SELECT * FROM tests WHERE id = ?", (int(nid[1:]),)).fetchone()
        return {"kind": "test", "id": nid, "title": x["product"], "row": dict(x)} if x else None
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
    return None
