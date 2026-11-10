"""Projects: every open top-level task with its subtasks, workers, deadline and blockers, on one page (T101).

    bin/jarvis projects                        the overview, most urgent first
    bin/jarvis projects --json                 the same as JSON (the dashboard reads /api/ops/projects)
    bin/jarvis projects deadline T<n> <date>   set (or `--clear`) a project's deadline

Deadlines live in brain/work/projects/deadlines.json; a date written in a task ("by 2026-11-20", "due 20 Nov")
counts too. Blockers are worked out, never typed: blocked or waiting-approval subtasks, pending approvals,
stopped or failed workers, an overdue deadline, and no movement for STALE_DAYS.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from . import paths, records

STALE_DAYS = 3
DUE = re.compile(r"\b(?:by|due|before|deadline:?)\s+(\d{4}-\d{2}-\d{2})\b", re.I)


def _deadlines_path() -> Path:
    d = paths.work_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d / "deadlines.json"


def deadlines() -> dict:
    try:
        return json.loads(_deadlines_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_deadline(conn, task, when: str | None) -> dict:
    tid = records.parse_task_id(task)
    records.task_get(conn, tid)  # raises if there is no such task
    d = deadlines()
    if when:
        try:
            date.fromisoformat(when)
        except ValueError as exc:
            raise records.RecordError("Give the deadline as YYYY-MM-DD.") from exc
        d[str(tid)] = when
    else:
        d.pop(str(tid), None)
    _deadlines_path().write_text(json.dumps(d, indent=1), encoding="utf-8")
    return d


def _days_since(ts: str | None, today: date) -> int | None:
    try:
        return (today - datetime.fromisoformat(ts).date()).days
    except (TypeError, ValueError):
        return None


def _descendants(conn, tid: int) -> list:
    rows, frontier = [], [tid]
    while frontier:
        found = conn.execute(f"SELECT * FROM tasks WHERE parent_id IN ({','.join('?' * len(frontier))})", frontier).fetchall()
        rows += found
        frontier = [r["id"] for r in found]
    return rows


def overview(conn, today: date | None = None, workers: list[dict] | None = None) -> dict:
    today = today or date.today()
    machines = []
    if workers is None:
        try:
            from . import work
            workers = work.listing(200)
        except Exception:
            workers = []
        try:  # T119: the other machine's workers count too, tagged W<n>@<machine>
            from . import twoway
            machines = twoway.other_machines()
            workers = workers + twoway.remote_workers()
        except Exception:
            pass
    dl = deadlines()
    open_st = records.OPEN_TASK_STATUSES
    tops = conn.execute(f"SELECT * FROM tasks WHERE parent_id IS NULL AND status IN ({','.join('?' * len(open_st))}) "
                        "ORDER BY priority, id", open_st).fetchall()
    pending = conn.execute("SELECT id, task_id, kind, summary FROM approvals WHERE status = 'pending'").fetchall()
    out = []
    for t in tops:
        subs = _descendants(conn, t["id"])
        ids = {t["id"], *(s["id"] for s in subs)}
        tagged = {f"T{i}" for i in ids}
        ws = [w for w in workers if str(w.get("task") or "").upper() in tagged]
        aps = [dict(a) for a in pending if a["task_id"] in ids]
        due = dl.get(str(t["id"]))
        if not due:
            m = DUE.search(f"{t['title']} {t['description'] or ''}")
            due = m.group(1) if m else None
        last = max([t["updated"]] + [s["updated"] for s in subs])
        idle = _days_since(last, today)
        blockers = [f"T{s['id']} {s['status'].replace('_', ' ')}: {s['title'][:60]}" for s in [t, *subs]
                    if s["status"] in ("blocked", "waiting_approval")]
        blockers += [f"A{a['id']} waiting for the owner: {a['summary'][:60]}" for a in aps]
        blockers += [f"W{w['id']} {w['status']}" for w in ws if w.get("status") in ("stopped", "failed")]
        days_left = (date.fromisoformat(due) - today).days if due else None
        if days_left is not None and days_left < 0:
            blockers.append(f"overdue by {-days_left} day(s)")
        if idle is not None and idle >= STALE_DAYS and not any(w.get("status") == "running" for w in ws):
            blockers.append(f"no movement for {idle} days")
        done = sum(1 for s in subs if s["status"] == "done")
        out.append({"id": f"T{t['id']}", "title": t["title"], "status": t["status"], "agent": t["agent"],
                    "product": t["product"], "priority": t["priority"], "created": t["created"], "updated": last,
                    "idle_days": idle, "deadline": due, "days_left": days_left,
                    "subtasks": {"total": len(subs), "done": done,
                                 "open": sum(1 for s in subs if s["status"] in open_st)},
                    "progress": round(done / len(subs), 2) if subs else (0.5 if t["status"] == "in_progress" else 0.0),
                    "workers": [{"id": f"W{w['id']}", "status": w.get("status")} for w in ws],
                    "approvals": [f"A{a['id']}" for a in aps], "blockers": blockers})
    # Most urgent first: overdue and near deadlines, then blocked, then running work, then priority.
    out.sort(key=lambda p: (p["days_left"] if p["days_left"] is not None else 999, not p["blockers"],
                            not any(w["status"] == "running" for w in p["workers"]), p["priority"]))
    return {"today": today.isoformat(), "projects": out, "machines": machines,
            "counts": {"open": len(out), "blocked": sum(1 for p in out if p["blockers"]),
                       "with_workers": sum(1 for p in out if any(w["status"] == "running" for w in p["workers"])),
                       "due_7_days": sum(1 for p in out if p["days_left"] is not None and 0 <= p["days_left"] <= 7),
                       "approvals_waiting": len(pending)}}


def lines(ov: dict) -> list[str]:
    c = ov["counts"]
    out = [f"Projects ({ov['today']}): {c['open']} open, {c['blocked']} with blockers, {c['with_workers']} with workers "
           f"running, {c['due_7_days']} due within 7 days, {c['approvals_waiting']} approvals waiting."]
    for p in ov["projects"]:
        sub = p["subtasks"]
        bits = [p["status"].replace("_", " ")]
        if sub["total"]:
            bits.append(f"{sub['done']}/{sub['total']} subtasks done")
        if p["workers"]:
            bits.append(" ".join(f"{w['id']} {w['status']}" for w in p["workers"]))
        if p["deadline"]:
            bits.append(f"due {p['deadline']}" + (f" ({p['days_left']}d)" if p["days_left"] is not None else ""))
        out.append(f"{p['id']} {p['title'][:80]}" + (f" [{p['product']}]" if p["product"] else ""))
        out.append(f"    {'; '.join(bits)}")
        out += [f"    ! {b}" for b in p["blockers"]]
    if ov.get("machines"):
        from . import twoway
        out.append("On the other machine:")
        out += [f"    {line}" for line in twoway.lines()]
    return out
