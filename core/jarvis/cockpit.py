"""The cockpit: everything the app's pages show, built once and shared by every open window (T110, D75).

The owner's priority is testing products properly and quickly, so the first sections are the testing pipeline
(every product on a rail of stages, what blocks it, the one next action) and the tests themselves (the numbers per
day against Blake's day 1/2/3 rules, "insufficient data" said plainly, the kill rule). The rest follows: what waits
for the owner (inbox), the work (plans, workers, desktop jobs), the money, capacity, the team's live activity,
memory and system status.

`state()` builds all of it in one pass (about 30 ms on the live brain). `Feed` keeps the last build, rebuilds when
the brain's fingerprint changes (or every couple of seconds while agents are working), and publishes only the
sections that changed on the live event stream, so a page never polls and never fetches section by section.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime

from . import db, jobs, journal, memory, pace, paths, projects, records, roster, routines, sales_watch, swarm, work

OPEN = records.OPEN_TASK_STATUSES

# The rail every product moves along (the jarvis-product-pipeline skill, steps 1 to 10).
STAGES = [("candidate", "Candidate"), ("research", "Researched"), ("creatives", "Creatives"), ("pdp", "Product page"),
          ("campaign", "Campaign draft"), ("approval", "Your approval"), ("live", "Live test"), ("verdict", "Verdict")]
# Whose finished work is the evidence for a stage.
STAGE_AGENTS = {"research": ("product-research", "market-research", "creative-research"),
                "creatives": ("creative-director",), "pdp": ("shopify", "store-qa"),
                "campaign": ("meta-ads", "product-testing")}
# Approvals that are a gate on a product (a `tool` approval is a permission prompt, not a gate).
GATE_KINDS = ("launch", "spend", "budget", "shopify_live", "price", "supplier", "other", "delete")
# Where Blake's day rules come from (Product Testing SOP p3 and the Blueprint), for the citation on the page.
DAY_RULE_SOURCE = "Blake, Product Testing SOP (M-261021-76bc) and Blueprint (M-261021-d278)"
FEEDBACK_TITLE = re.compile(r"^Feedback \(([^)]+)\):\s*(.*)$")


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _div(a, b, places: int = 4):
    return round(a / b, places) if b else None


def signature(conn) -> tuple:
    """Cheap fingerprint of the brain: changes whenever anything the pages show changes."""
    parts = []
    for table in ("tasks", "task_events", "agent_runs", "journal", "approvals", "decisions", "memory", "costs",
                  "products", "tests", "test_metrics", "money", "jobs", "actions"):
        parts.append(conn.execute(f"SELECT COUNT(*), MAX(rowid) FROM {table}").fetchone()[:])
    parts.append(conn.execute("SELECT MAX(updated) FROM tasks").fetchone()[0])
    parts.append(conn.execute("SELECT MAX(updated) FROM memory").fetchone()[0])
    parts.append(conn.execute("SELECT MAX(updated) FROM products").fetchone()[0])
    parts.append(db.get_meta(conn, "claude_login_needed"))
    parts.append(db.get_meta(conn, "usage"))
    parts.append(conn.execute("SELECT GROUP_CONCAT(status) FROM approvals WHERE id > (SELECT MAX(id) - 30 FROM approvals)").fetchone()[0])
    parts.append(conn.execute("SELECT GROUP_CONCAT(status) FROM tests").fetchone()[0])
    parts.append(conn.execute("SELECT GROUP_CONCAT(status) FROM jobs WHERE id > (SELECT MAX(id) - 30 FROM jobs)").fetchone()[0])
    return tuple(parts)


# ---------------------------------------------------------------- the testing pipeline

def _card(p, tasks: list, tests: list, pending: list, approved: list, workers: list[dict]) -> dict:
    slug = p["slug"]
    mine = [t for t in tasks if t["product"] == slug]
    my_tests = [x for x in tests if x["product"] == slug]
    finished = [x for x in my_tests if x["status"] in ("stopped", "passed", "failed")]
    live = next((x for x in reversed(my_tests) if x["status"] in ("running", "extended")), None)
    planned = next((x for x in reversed(my_tests) if x["status"] == "planned"), None)
    # A finished test closes a round: what was done before it is carried over, not counted as this round's work.
    cutoff = max((x["ended"] or "" for x in finished), default="")
    in_round = [t for t in mine if t["status"] in OPEN or (t["status"] == "done" and (t["completed"] or "") > cutoff)]
    earlier = [t for t in mine if t["status"] == "done" and (t["completed"] or "") <= cutoff] if cutoff else []
    open_tasks = [t for t in mine if t["status"] in OPEN]
    ids = {f"T{t['id']}" for t in open_tasks}
    on_it = [w for w in workers if str(w.get("task") or "").upper() in ids]
    running = {str(w.get("task") or "").upper() for w in on_it if w.get("status") == "running"}
    gates = [a for a in pending if a["product"] == slug and a["kind"] in GATE_KINDS]

    stages = []
    for key, label in STAGES:
        st = {"key": key, "label": label, "state": "todo", "open": 0, "done": 0, "tasks": []}
        agents = STAGE_AGENTS.get(key)
        if key == "candidate":
            st["state"] = "done"
        elif agents:
            ts = [t for t in in_round if t["agent"] in agents]
            open_ = [t for t in ts if t["status"] in OPEN]
            done = [t for t in ts if t["status"] == "done"]
            st.update(open=len(open_), done=len(done), tasks=[f"T{t['id']}" for t in open_][:5])
            if any(t["status"] in ("blocked", "waiting_approval") for t in open_):
                st["state"] = "blocked"
            elif any(t["status"] == "in_progress" or f"T{t['id']}" in running for t in open_):
                st["state"] = "active"
            elif open_:
                st["state"] = "queued"
            elif done:
                st["state"] = "done"
            elif any(t["agent"] in agents for t in earlier):
                st["state"] = "carried"
        elif key == "approval":
            if gates:
                st.update(state="waiting", tasks=[f"A{a['id']}" for a in gates])
            elif live or any(a["product"] == slug and (a["decided_ts"] or "") > cutoff for a in approved):
                st["state"] = "done"
        elif key == "live":
            st["state"] = "active" if live else "queued" if planned else "todo"
            if live or planned:
                st["tasks"] = [f"X{(live or planned)['id']}"]
        stages.append(st)

    by_key = {s["key"]: s for s in stages}
    work_keys = list(STAGE_AGENTS)
    moving = bool(running) or any(t["status"] == "in_progress" for t in open_tasks)
    if live:
        at = "live"
    elif gates:
        at = "approval"
    elif not mine and not my_tests:
        at = "candidate"
    else:
        at = next((k for k in work_keys if by_key[k]["state"] in ("blocked", "active", "queued")), None)
        if not at and finished and all(by_key[k]["state"] in ("todo", "carried") for k in work_keys):
            at = "verdict"   # the last test is called and no stage has been worked on since
        if not at:
            at = next((k for k in work_keys if by_key[k]["state"] == "todo"), "live" if planned else "approval")
    by_key[at]["here"] = True
    if finished and at == "verdict":
        by_key["verdict"]["state"] = "done"

    blockers = [f"T{t['id']} {t['status'].replace('_', ' ')}: {journal.short(t['title'], 80)}"
                for t in open_tasks if t["status"] == "blocked" or (t["status"] == "waiting_approval" and not gates)]
    blockers += [f"A{a['id']} is waiting for you: {journal.short(a['summary'], 80)}" for a in gates]
    blockers += [f"W{w['id']} {w['status']} on {w.get('task')}" for w in on_it if w.get("status") in ("stopped", "failed")]
    if open_tasks and not moving and not live and not gates:
        blockers.append(f"Nothing is running: {len(open_tasks)} task{'s' if len(open_tasks) != 1 else ''} queued, none started")

    first_open = next((t for t in open_tasks if at in STAGE_AGENTS and t["agent"] in STAGE_AGENTS[at]),
                      open_tasks[0] if open_tasks else None)
    if gates:
        g = gates[0]
        nxt = {"kind": "approve", "approval": g["id"], "label": f"Decide A{g['id']}: {journal.short(g['summary'], 90)}"}
    elif live:
        nxt = {"kind": "read", "test": f"X{live['id']}", "label": f"Read X{live['id']} against Blake's day rule"}
    elif at == "candidate":
        nxt = {"kind": "research", "label": "Start product research"}
    elif moving:
        t = next((t for t in open_tasks if t["status"] == "in_progress" or f"T{t['id']}" in running), first_open)
        nxt = {"kind": "watch", "task": f"T{t['id']}", "label": f"In progress: T{t['id']} {journal.short(t['title'], 70)}"}
    elif at == "verdict":
        nxt = {"kind": "debate", "label": "Decide what happens next: retest with new creatives, or drop"}
    elif first_open:
        nxt = {"kind": "start", "task": f"T{first_open['id']}",
               "label": f"Start T{first_open['id']}: {journal.short(first_open['title'], 70)}"}
    else:
        nxt = {"kind": "pipeline", "label": "Run the product pipeline"}

    last = finished[-1] if finished else None
    return {"slug": slug, "name": p["name"], "stage": p["stage"], "notes": p["notes"], "updated": p["updated"],
            "archived": p["stage"] == "archived", "round": len(finished) + 1, "at": at, "stages": stages,
            "blockers": blockers, "next": nxt, "moving": moving or bool(live),
            "last_test": ({"id": f"X{last['id']}", "status": last["status"], "verdict": last["verdict"],
                           "ended": last["ended"]} if last else None),
            "open": [{"id": f"T{t['id']}", "title": t["title"], "agent": t["agent"], "status": t["status"],
                      "worker": next((f"W{w['id']}" for w in on_it if str(w.get("task")).upper() == f"T{t['id']}"
                                      and w.get("status") == "running"), None)} for t in open_tasks[:8]],
            "open_count": len(open_tasks)}


def pipeline(conn, workers: list[dict]) -> dict:
    products = conn.execute("SELECT * FROM products ORDER BY (stage = 'archived'), updated DESC").fetchall()
    tasks = conn.execute("SELECT id, parent_id, title, agent, product, status, created, updated, completed FROM tasks"
                         " WHERE product IS NOT NULL AND status != 'cancelled' ORDER BY id").fetchall()
    tests = conn.execute("SELECT * FROM tests ORDER BY id").fetchall()
    pending = conn.execute("SELECT a.*, t.product AS product FROM approvals a LEFT JOIN tasks t ON t.id = a.task_id"
                           " WHERE a.status = 'pending' ORDER BY a.id").fetchall()
    approved = conn.execute("SELECT a.id, a.decided_ts, t.product AS product FROM approvals a JOIN tasks t ON t.id = a.task_id"
                            " WHERE a.status = 'approved' AND a.kind IN ('launch', 'spend', 'shopify_live')").fetchall()
    cards = [_card(p, tasks, tests, pending, approved, workers) for p in products]
    # Research with no product registered yet (the hunt for the next candidate) is the front of the pipeline too.
    marks = ",".join("?" * len(OPEN))
    by_task = {str(w.get("task") or "").upper(): w for w in workers if w.get("status") == "running"}
    researching = {int(k[1:]) for k, w in by_task.items() if w.get("agent") in STAGE_AGENTS["research"] and k[1:].isdigit()}
    hunts = [{"id": f"T{r['id']}", "title": r["title"], "status": r["status"], "updated": r["updated"],
              "worker": (f"W{by_task[f'T{r['id']}']['id']}" if f"T{r['id']}" in by_task else None)}
             for r in conn.execute(f"SELECT id, title, status, agent, updated FROM tasks WHERE product IS NULL AND status IN ({marks})"
                                   " ORDER BY id DESC", OPEN)
             if r["agent"] == "product-research" or r["id"] in researching][:6]
    return {"stages": [{"key": k, "label": label} for k, label in STAGES],
            "products": [c for c in cards if not c["archived"]], "archived": [c for c in cards if c["archived"]],
            "hunts": hunts}


# ---------------------------------------------------------------- tests against Blake's day rules

def day_call(day: int, atc: float, checkouts: float, purchases: float, roas, breakeven) -> tuple[str, str]:
    """Blake's cut-or-continue call for a test day, judged on the totals so far. His counts are examples, not
    thresholds, so this only separates none from some; profit is judged against the breakeven line."""
    if day <= 1:
        return ("cut", "Zero add-to-carts on day 1: cut.") if not atc else \
            ("continue", "Buying intent on day 1: continue and add 5 to 10 creatives.")
    if day == 2:
        if purchases:
            return "continue", "Sales by day 2: continue."
        if checkouts:
            return "diagnose", "Checkouts but no sale: remove checkout friction now, then split-test the offer."
        return "cut", "No checkouts by day 2: stop."
    if not purchases:
        return "cut", "No sales by day 3: cut and go back to research."
    if breakeven and roas is not None and roas >= breakeven:
        return "scale", "Profitable at the starting budget: the budget may go up (golden rule)."
    return "continue", "Sales but not yet profitable: continue and bring the cost per sale down with creatives."


def tests_view(conn) -> list[dict]:
    be = sales_watch.breakeven()["value"]
    cfg = paths.config()
    out = []
    for t in conn.execute("SELECT * FROM tests ORDER BY (status IN ('running', 'extended')) DESC, id DESC LIMIT 8"):
        rows = conn.execute("SELECT * FROM test_metrics WHERE test_id = ? ORDER BY id", (t["id"],)).fetchall()
        latest, order = {}, []
        for r in rows:   # a day read several times keeps its last read
            if r["day"] not in latest:
                order.append(r["day"])
            latest[r["day"]] = r
        cum = dict.fromkeys(("spend", "impressions", "clicks", "atc", "checkouts", "purchases", "revenue"), 0.0)
        days = []
        for n, label in enumerate(order, 1):
            r = latest[label]
            d = {"spend": r["spend"] or 0, "impressions": r["impressions"] or 0, "clicks": r["clicks"] or 0,
                 "atc": r["add_to_cart"] or 0, "checkouts": r["checkouts"] or 0, "purchases": r["purchases"] or 0,
                 "revenue": r["revenue"] or 0}
            for k, v in d.items():
                cum[k] += v
            call, why = day_call(n, cum["atc"], cum["checkouts"], cum["purchases"], _div(cum["revenue"], cum["spend"]), be)
            days.append({"n": n, "day": label, **d, "cpc": _div(d["spend"], d["clicks"], 2), "ctr": _div(d["clicks"], d["impressions"]),
                         "cpa": _div(d["spend"], d["purchases"], 2), "roas": _div(d["revenue"], d["spend"], 2),
                         "call": call, "why": why, "recorded": r["recorded"], "source": journal.short(r["source"] or "", 160)})
        ev = records.evaluate(conn, t["id"])
        warnings = []
        if len(rows) > len(order):
            n = len(rows) - len(order)
            warnings.append(f"{n} earlier partial read{'s' if n != 1 else ''} replaced by the latest read of the same day.")
        if any(str(d).isdigit() for d in order) and any(not str(d).isdigit() for d in order):
            warnings.append("Days are labelled two ways (day numbers and dates), so spend may be counted twice. "
                            "Check the reads before trusting the totals.")
        live = t["status"] in ("running", "extended")
        day_n = None
        if live and t["started"]:
            try:
                day_n = (datetime.now().astimezone() - datetime.fromisoformat(t["started"])).days + 1
            except ValueError:
                day_n = None
        ads = []
        for m in conn.execute("SELECT id, title, updated FROM memory WHERE product = ? AND status = 'active'"
                              " AND tags LIKE '%kind:ad%' ORDER BY updated DESC, id DESC LIMIT 14", (t["product"],)):
            hit = FEEDBACK_TITLE.match(m["title"] or "")
            ads.append({"id": m["id"], "verdict": (hit.group(1) if hit else "").lower(),
                        "title": hit.group(2) if hit else m["title"], "updated": m["updated"]})
        try:
            pnl = records.test_pnl(conn, t["id"])
        except records.RecordError:
            pnl = None
        out.append({
            "id": f"X{t['id']}", "product": t["product"], "status": t["status"], "live": live, "day_n": day_n,
            "hypothesis": t["hypothesis"], "angle": t["angle"], "budget_usd": t["budget_usd"],
            "planned_days": t["planned_days"], "criteria": t["criteria"], "started": t["started"], "ended": t["ended"],
            "verdict": t["verdict"], "lesson": t["lesson"], "days": days, "reads": len(rows), "totals": ev,
            "sufficient": bool(ev["sufficient"]) if rows else False,
            "minimums": {"spend_usd": cfg["min_spend_for_verdict_usd"], "clicks": cfg["min_clicks_for_verdict"]},
            "breakeven": be, "roas_verdict": None if be is None or ev["roas"] is None else "above" if ev["roas"] >= be else "below",
            "call": days[-1]["call"] if days else None, "why": days[-1]["why"] if days else None,
            "rule_source": DAY_RULE_SOURCE, "warnings": warnings, "ads": ads, "pnl": pnl})
    return out


# ---------------------------------------------------------------- what waits for the owner

def inbox(conn, runner, workers: list[dict], job_rows: list[dict], figures: dict, settings: dict) -> list[dict]:
    items = []
    if db.get_meta(conn, "claude_login_needed"):
        items.append({"kind": "login", "key": "login", "title": "Claude Code is signed out on this PC",
                      "detail": "Open a terminal, run claude once and sign in. Until then JARVIS can't answer."})
    waiting = runner.permissions.listing() if runner else []
    seen = set()
    for p in waiting:
        seen.add(p.get("approval"))
        if p["question"]:
            items.append({"kind": "question", "key": f"q:{p['id']}", "id": p["id"], "title": "JARVIS has a question",
                          "questions": [{"question": q.get("question", ""), "options": [o.get("label", "") for o in q.get("options") or []]}
                                        for q in (p.get("input") or {}).get("questions") or []]})
        else:
            items.append({"kind": "permission", "key": f"p:{p['id']}", "id": p["id"], "approval": p.get("approval"),
                          "title": p["summary"], "since": p.get("since")})
    for a in conn.execute("SELECT a.*, t.product AS product FROM approvals a LEFT JOIN tasks t ON t.id = a.task_id"
                          " WHERE a.status = 'pending' ORDER BY a.id"):
        if a["id"] in seen:
            continue
        items.append({"kind": "approval", "key": f"A{a['id']}", "approval": a["id"], "gate": a["kind"],
                      "title": a["summary"], "detail": a["detail"], "amount": a["amount"], "currency": a["currency"],
                      "task": f"T{a['task_id']}" if a["task_id"] else None, "product": a["product"], "ts": a["ts"]})
    kill = float(settings.get("sales_kill_aud") or 0)
    if kill and figures["spend_since_sale_aud"] >= kill:
        items.append({"kind": "kill", "key": "kill", "title": f"A${figures['spend_since_sale_aud']:.0f} on ads over "
                      f"{figures['days_without_sale']} day(s) without a sale: your kill rule (A${kill:.0f}) is hit",
                      "detail": "Blake: by day 3 there must be sales, otherwise cut and go back to research."})
    for w in workers:
        if w.get("status") in ("stopped", "failed"):
            items.append({"kind": "worker", "key": f"W{w['id']}", "id": f"W{w['id']}", "status": w["status"],
                          "task": w.get("task") or None, "title": f"W{w['id']} {w['status']}: {swarm._brief_title(w.get('brief', ''))}",
                          "detail": "It stopped before finishing." if w["status"] == "stopped"
                          else f"It gave up after {w.get('attempts', 0)} attempts."})
    recent = (datetime.now().astimezone().timestamp() - 2 * 86400)
    failed = [j for j in job_rows if j["status"] == "failed" and _epoch(j.get("finished")) >= recent]
    if len(failed) == 1:
        j = failed[0]
        items.append({"kind": "job", "key": f"J{j['id']}", "id": f"J{j['id']}", "title": f"Desktop job J{j['id']} failed: "
                      f"{journal.short(j['text'], 90)}", "detail": journal.short(j.get("summary") or "", 300)})
    elif failed:   # several at once is usually one cause (the runner restarted): one line, not a wall of them
        items.append({"kind": "jobs", "key": "jobs:" + ",".join(str(j["id"]) for j in failed),
                      "title": f"{len(failed)} desktop jobs failed in the last 2 days: " + ", ".join(f"J{j['id']}" for j in failed),
                      "detail": journal.short(failed[0].get("summary") or "", 240)})
    queued = sum(1 for j in job_rows if j["status"] == "queued")
    if queued and not jobs.runner_alive():
        items.append({"kind": "runner", "key": "runner", "title": f"{queued} desktop job{'s' if queued != 1 else ''} queued, "
                      "but the job runner is off", "detail": "Jobs that need the screen wait until the job runner session "
                      "is started in the Claude desktop app on the PC."})
    return items


def _epoch(ts) -> float:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- work: plans, workers, jobs

def work_view(conn, runner, workers: list[dict], job_rows: list[dict], live: dict) -> dict:
    by_key = {a["key"]: a for a in live["agents"]}
    default_model = paths.config().get("worker_model") or "opus"
    tm = roster.task_models()
    ov = projects.overview(conn, workers=workers)
    plans = []
    for p in ov["projects"][:60]:
        tid = int(p["id"][1:])
        top = conn.execute("SELECT description, deliverable, summary FROM tasks WHERE id = ?", (tid,)).fetchone()
        subs = conn.execute("SELECT id, title, agent, status, deliverable, summary, updated FROM tasks WHERE parent_id = ?"
                            " ORDER BY id", (tid,)).fetchall()
        plans.append({**p, "description": journal.short(top["description"] or "", 400), "deliverable": top["deliverable"],
                      "model": tm.get(p["id"]),
                      "steps": [{"id": f"T{s['id']}", "title": s["title"], "agent": s["agent"], "status": s["status"],
                                 "deliverable": s["deliverable"], "summary": journal.short(s["summary"] or "", 200),
                                 "updated": s["updated"], "model": tm.get(f"T{s['id']}"),
                                 "worker": next((f"W{w['id']}" for w in workers if str(w.get("task") or "").upper() == f"T{s['id']}"
                                                 and w.get("status") == "running"), None)} for s in subs]})
    ws = []
    for w in workers[:16]:
        a = by_key.get(f"W{w['id']}") or {}
        ws.append({"id": f"W{w['id']}", "task": w.get("task") or None, "agent": w.get("agent") or "general-purpose",
                   "status": w.get("status"), "model": w.get("model") or default_model, "model_default": not w.get("model"),
                   "started": w.get("started"), "updated": w.get("updated"), "attempts": w.get("attempts", 0),
                   "title": swarm._brief_title(w.get("brief", "")), "doing": a.get("doing"), "progress": a.get("progress"),
                   "actions": a.get("actions"), "last": a.get("last")})
    recent = conn.execute("SELECT id, kind, summary, status, decided_ts, decision_note FROM approvals WHERE status != 'pending'"
                          " AND kind != 'tool' ORDER BY decided_ts DESC LIMIT 6").fetchall()
    return {"plans": plans, "counts": ov["counts"], "workers": ws, "worker_model": default_model,
            "jobs": [{"id": f"J{j['id']}", "text": j["text"], "status": j["status"], "source": j.get("source"),
                      "ts": j["ts"], "finished": j.get("finished"), "summary": journal.short(j.get("summary") or "", 240)}
                     for j in job_rows],
            "job_runner": jobs.runner_alive(), "decided": _rows(recent),
            "queue": [journal.short(m["text"], 140) for m in (runner.queue if runner else [])]}


# ---------------------------------------------------------------- money, capacity, memory, system

def money_view(conn, sales: dict, roas: dict) -> dict:
    today = paths.now()[:10]
    month = records.money_report(conn, today[:8] + "01", today)
    ok, spent, budget = records.cost_check(conn, 0)
    ledger = conn.execute("SELECT day, kind, category, amount_aud, product, note, source FROM money ORDER BY day DESC, id DESC"
                          " LIMIT 14").fetchall()
    pnl = []
    for t in conn.execute("SELECT id FROM tests ORDER BY id DESC LIMIT 6"):
        try:
            pnl.append(records.test_pnl(conn, t["id"]))
        except records.RecordError:
            continue
    return {"figures": sales["figures"], "roas": roas, "sources": {"shopify": sales["shopify"], "meta": sales["meta"]},
            "last_watch": sales["last_watch"], "announcements": sales["announcements"], "daily": roster.daily_profit(conn),
            "month": month, "ai": {"spent": spent, "budget": budget, "ok": ok}, "ledger": _rows(ledger), "tests": pnl,
            "kill_aud": sales["settings"].get("sales_kill_aud")}


def brain_view(conn) -> dict:
    cats = conn.execute("SELECT category, COUNT(*) n FROM memory WHERE status IN ('active', 'needs-review')"
                        " GROUP BY category ORDER BY n DESC").fetchall()
    keep = "id, title, category, product, provenance, status, updated"
    return {"total": sum(r["n"] for r in cats), "categories": [{"name": r["category"], "n": r["n"]} for r in cats],
            "needs_review": conn.execute("SELECT COUNT(*) FROM memory WHERE status = 'needs-review'").fetchone()[0],
            "recent": _rows(conn.execute(f"SELECT {keep} FROM memory WHERE status = 'active' ORDER BY updated DESC LIMIT 10")),
            "decisions": _rows(records.decision_list(conn, limit=12)),
            "journal": [{"ts": r["ts"], "role": r["role"], "text": journal.short(r["text"], 400)}
                        for r in journal.recent(conn, limit=24)]}


def system_view(conn, server) -> dict:
    cfg = paths.config()
    runner = getattr(server, "runner", None)
    out = {"routines": routines.status(check_scheduler=False), "connectors": roster.connectors(),
           "actions": roster.recent_actions(conn, 8), "models": list(roster.MODELS), "brain": str(paths.home()),
           "agents": sorted(roster.agents()),
           "conversation": {"model": roster.model_alias(cfg.get("dashboard_model")) if cfg.get("dashboard_model") else "",
                            "effort": cfg.get("dashboard_effort") or "", "session": runner.session() if runner else None,
                            "warm": bool(runner and runner.proc and runner.proc.poll() is None)},
           "login_needed": bool(db.get_meta(conn, "claude_login_needed")), "job_runner": jobs.runner_alive()}
    if server is not None:
        from .dashboard import find_claude
        out.update(claude=bool(find_claude()), tts=server.tts.info(),
                   wake={**server.wake.info(), "enabled": server.wake.enabled()}, telegram=server.telegram.info())
    return out


def live_view(conn, runner, workers: list[dict] | None = None) -> dict:
    """Who is working this minute and what they just did (swarm.py reads each worker's log as it grows)."""
    s = swarm.state(conn, runner.live if runner else None, workers)
    for a in s["agents"]:
        a.pop("ticks", None)
        a.pop("words", None)
    return {"count": s["live"], "agents": s["agents"], "feed": s["feed"][-60:],
            "jarvis": {"status": runner.live.get("status", "idle") if runner else "idle",
                       "request": journal.short(runner.live.get("request") or "", 200) if runner else ""}}


def state(conn, server=None) -> dict:
    """Every section the app shows. `server` carries the runner, voice, wake word and phone bridge when there is one."""
    runner = getattr(server, "runner", None)
    workers = work.listing(40)
    job_rows = _rows(jobs.listing(conn, 14))
    sales = sales_watch.status(conn)
    roas = sales_watch.roas(conn)
    live = live_view(conn, runner, workers)
    cap = pace.status(conn)
    cap["resets"] = cap["resets"][:16]   # to the minute: an estimated reset moves every second and isn't news
    f = sales["figures"]
    items = inbox(conn, runner, workers, job_rows, f, sales["settings"])
    return {
        "kpis": {"sales_aud": f["sales_aud"], "sales_count": f["sales_count"], "spend_aud": f["ad_spend_aud"],
                 "profit_today_aud": f["profit_today_aud"], "profit_week_aud": f["profit_week_aud"],
                 "weekly_target_aud": f["weekly_target_aud"], "roas_today": roas["today"]["roas"],
                 "roas_week": roas["week"]["roas"], "breakeven": roas["breakeven"], "live": roas["live"],
                 "why_not_live": roas["why_not_live"], "age_minutes": roas["age_minutes"],
                 "capacity_used_pct": cap["used_pct"], "capacity_ceiling_pct": cap["ceiling_pct"],
                 "agents_live": live["count"], "inbox": len(items)},
        "pipeline": pipeline(conn, workers),
        "tests": tests_view(conn),
        "inbox": items,
        "work": work_view(conn, runner, workers, job_rows, live),
        "live": live,
        "money": money_view(conn, sales, roas),
        "capacity": cap,
        "brain": brain_view(conn),
        "system": system_view(conn, server),
    }


# ---------------------------------------------------------------- one build, shared by every window

class Feed:
    """Keeps the latest state and pushes the sections that changed to every open window."""

    def __init__(self, server):
        self.server = server
        self.sections: dict = {}
        self.hashes: dict[str, str] = {}
        self.built = 0.0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def build(self) -> dict:
        """Rebuild; returns only the sections whose content changed."""
        conn = db.connect()
        try:
            data = state(conn, self.server)
        finally:
            conn.close()
        changed = {}
        with self.lock:
            for name, value in data.items():
                digest = hashlib.md5(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
                if self.hashes.get(name) != digest:
                    self.hashes[name] = digest
                    changed[name] = value
            self.sections, self.built = data, time.time()
        return changed

    def snapshot(self, max_age: float = 1.0) -> dict:
        """The whole state, rebuilt if the last build is older than `max_age` seconds."""
        if time.time() - self.built > max_age:
            changed = self.build()
            if changed and self.thread:
                self.server.hub.publish({"type": "state", "sections": changed})
        return self.sections

    def start(self) -> None:
        with self.lock:
            if self.thread or self.stop.is_set():
                return
            self.thread = threading.Thread(target=self._loop, name="jarvis-feed", daemon=True)
            self.thread.start()

    def close(self) -> None:
        self.stop.set()

    def _loop(self) -> None:
        conn = db.connect()
        last_sig, last_sync, last_warm = None, time.time(), time.time()
        try:
            while not self.stop.wait(1.0):
                runner, hub = self.server.runner, self.server.hub
                now = time.time()
                if getattr(self.server, "keep_warm", False) and now - last_warm > 30:
                    last_warm = now
                    runner.warm()   # Claude Code ended while idle (stopped, crashed): start it before the next message
                if not hub.subs:
                    continue   # nobody is looking
                try:
                    if now - last_sync > 60:   # entries written by other sessions reach the index
                        last_sync = now
                        memory.sync(conn)
                    sig = (signature(conn), runner.live.get("status"), len(runner.permissions.pending), len(runner.queue),
                           tuple(runner.live.get("agents") or ()))
                    working = runner.busy or (self.sections.get("live") or {}).get("count")
                    if sig == last_sig and now - self.built < (2 if working else 10):
                        continue
                    last_sig = sig
                    changed = self.build()
                    if changed:
                        hub.publish({"type": "state", "sections": changed})
                except Exception as exc:   # a bad row must never end the stream for every window
                    import sys
                    sys.stderr.write(f"jarvis: feed skipped a beat: {exc!r}\n")
        finally:
            conn.close()
