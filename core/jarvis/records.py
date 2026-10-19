"""Structured records: products, tasks, decisions, approvals, tests, costs and logs."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import paths
from .memory import slugify

TASK_STATUSES = ("todo", "in_progress", "blocked", "waiting_approval", "done", "cancelled")
OPEN_TASK_STATUSES = ("todo", "in_progress", "blocked", "waiting_approval")
PRODUCT_STAGES = ("idea", "researching", "preparing", "ready", "testing", "passed", "failed", "scaling", "archived")
APPROVAL_KINDS = ("spend", "launch", "budget", "shopify_live", "price", "supplier", "delete", "other")
TEST_STATUSES = ("planned", "running", "stopped", "extended", "passed", "failed")


class RecordError(Exception):
    pass


def _one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RecordError("Not found")
    return row


def parse_task_id(value) -> int:
    text = str(value).upper().lstrip("T")
    if not text.isdigit():
        raise RecordError(f"Bad task id '{value}' (expected e.g. T12)")
    return int(text)


# ---------------------------------------------------------------- products

def product_add(conn, name: str, stage="idea", notes=None) -> str:
    slug = slugify(name, 40)
    if stage not in PRODUCT_STAGES:
        raise RecordError(f"Unknown stage '{stage}'")
    stamp = paths.now()
    try:
        conn.execute("INSERT INTO products VALUES (?,?,?,?,?,?)", (slug, name, stage, notes, stamp, stamp))
    except sqlite3.IntegrityError:
        raise RecordError(f"Product '{slug}' already exists")
    conn.commit()
    return slug


def product_update(conn, slug: str, stage=None, notes=None) -> None:
    _one(conn, "SELECT slug FROM products WHERE slug = ?", (slug,))
    if stage and stage not in PRODUCT_STAGES:
        raise RecordError(f"Unknown stage '{stage}'")
    conn.execute(
        "UPDATE products SET stage = COALESCE(?, stage), notes = COALESCE(?, notes), updated = ? WHERE slug = ?",
        (stage, notes, paths.now(), slug),
    )
    conn.commit()


def require_product(conn, slug: str | None) -> None:
    if slug and not conn.execute("SELECT 1 FROM products WHERE slug = ?", (slug,)).fetchone():
        raise RecordError(f"Unknown product '{slug}'. Add it first: jarvis product add \"<name>\"")


# ---------------------------------------------------------------- tasks

def task_add(conn, title, *, agent=None, product=None, parent=None, description=None, priority=2) -> int:
    require_product(conn, product)
    parent_id = parse_task_id(parent) if parent else None
    if parent_id:
        _one(conn, "SELECT id FROM tasks WHERE id = ?", (parent_id,))
    stamp = paths.now()
    cur = conn.execute(
        """INSERT INTO tasks (parent_id, title, description, agent, product, priority, created, updated)
           VALUES (?,?,?,?,?,?,?,?)""",
        (parent_id, title, description, agent, product, priority, stamp, stamp),
    )
    task_id = cur.lastrowid
    _event(conn, task_id, "created", f"agent={agent or '-'}")
    conn.commit()
    return task_id


def _event(conn, task_id, kind, note=None):
    conn.execute("INSERT INTO task_events (task_id, ts, kind, note) VALUES (?,?,?,?)",
                 (task_id, paths.now(), kind, note))


def task_update(conn, task_id, *, status=None, note=None, deliverable=None, summary=None, agent=None) -> None:
    tid = parse_task_id(task_id)
    _one(conn, "SELECT id FROM tasks WHERE id = ?", (tid,))
    if status and status not in TASK_STATUSES:
        raise RecordError(f"Unknown status '{status}'. Use one of: {', '.join(TASK_STATUSES)}")
    stamp = paths.now()
    conn.execute(
        """UPDATE tasks SET status = COALESCE(?, status), deliverable = COALESCE(?, deliverable),
           summary = COALESCE(?, summary), agent = COALESCE(?, agent), updated = ?,
           completed = CASE WHEN ? IN ('done','cancelled') THEN ? ELSE completed END
           WHERE id = ?""",
        (status, deliverable, summary, agent, stamp, status, stamp, tid),
    )
    if status:
        _event(conn, tid, f"status:{status}", note)
    elif note:
        _event(conn, tid, "note", note)
    if deliverable:
        _event(conn, tid, "deliverable", deliverable)
    conn.commit()


def task_get(conn, task_id) -> tuple[sqlite3.Row, list, list]:
    tid = parse_task_id(task_id)
    task = _one(conn, "SELECT * FROM tasks WHERE id = ?", (tid,))
    events = conn.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (tid,)).fetchall()
    children = conn.execute("SELECT * FROM tasks WHERE parent_id = ? ORDER BY id", (tid,)).fetchall()
    return task, events, children


def task_list(conn, *, status=None, agent=None, product=None, open_only=False, limit=30) -> list:
    filters, params = [], []
    if open_only:
        filters.append(f"status IN ({','.join('?' * len(OPEN_TASK_STATUSES))})")
        params.extend(OPEN_TASK_STATUSES)
    for col, val in (("status", status), ("agent", agent), ("product", product)):
        if val:
            filters.append(f"{col} = ?")
            params.append(val)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return conn.execute(
        f"SELECT * FROM tasks {where} ORDER BY priority, id DESC LIMIT ?", [*params, limit]
    ).fetchall()


def task_workpath(conn, task_id):
    """Where this task's deliverable should be saved (folder is created)."""
    task, _, _ = task_get(conn, task_id)
    folder = paths.work_dir() / (task["product"] or "general")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"T{task['id']}-{slugify(task['title'], 40)}.md"


# ---------------------------------------------------------------- decisions and approvals

def decision_add(conn, decision, *, rationale=None, product=None, task=None, decided_by="user") -> int:
    require_product(conn, product)
    cur = conn.execute(
        "INSERT INTO decisions (ts, product, task_id, decision, rationale, decided_by) VALUES (?,?,?,?,?,?)",
        (paths.now(), product, parse_task_id(task) if task else None, decision, rationale, decided_by),
    )
    conn.commit()
    return cur.lastrowid


def approval_request(conn, kind, summary, *, detail=None, task=None, amount=None, currency=None) -> int:
    if kind not in APPROVAL_KINDS:
        raise RecordError(f"Unknown approval kind '{kind}'. Use one of: {', '.join(APPROVAL_KINDS)}")
    tid = parse_task_id(task) if task else None
    cur = conn.execute(
        "INSERT INTO approvals (ts, task_id, kind, summary, detail, amount, currency) VALUES (?,?,?,?,?,?,?)",
        (paths.now(), tid, kind, summary, detail, amount, currency),
    )
    if tid:
        task_update(conn, tid, status="waiting_approval", note=f"approval A{cur.lastrowid}: {summary}")
    conn.commit()
    return cur.lastrowid


def approval_decide(conn, approval_id, approved: bool, note=None) -> sqlite3.Row:
    aid = int(str(approval_id).upper().lstrip("A"))
    row = _one(conn, "SELECT * FROM approvals WHERE id = ?", (aid,))
    if row["status"] != "pending":
        raise RecordError(f"A{aid} was already {row['status']}")
    status = "approved" if approved else "rejected"
    conn.execute("UPDATE approvals SET status = ?, decided_ts = ?, decision_note = ? WHERE id = ?",
                 (status, paths.now(), note, aid))
    if row["task_id"]:
        _event(conn, row["task_id"], f"approval:{status}", f"A{aid} {note or ''}".strip())
    conn.commit()
    return _one(conn, "SELECT * FROM approvals WHERE id = ?", (aid,))


# ---------------------------------------------------------------- product tests

def test_add(conn, product, hypothesis, *, angle=None, budget_usd=None, days=None, criteria=None) -> int:
    require_product(conn, product)
    cfg = paths.config()
    cur = conn.execute(
        """INSERT INTO tests (product, hypothesis, angle, budget_usd, planned_days, criteria, created)
           VALUES (?,?,?,?,?,?,?)""",
        (product, hypothesis, angle, budget_usd if budget_usd is not None else cfg["default_test_budget_usd"],
         days or cfg["default_test_days"], criteria, paths.now()),
    )
    conn.commit()
    return cur.lastrowid


def test_update(conn, test_id, *, status=None, verdict=None, lesson=None) -> None:
    tid = int(str(test_id).upper().lstrip("X"))
    row = _one(conn, "SELECT * FROM tests WHERE id = ?", (tid,))
    if status and status not in TEST_STATUSES:
        raise RecordError(f"Unknown test status '{status}'")
    if status in ("passed",) and evaluate(conn, tid)["sufficient"] is False:
        raise RecordError("Not enough data to call this test passed (see `jarvis test show`). "
                          "Record more metrics or mark it 'extended'.")
    stamp = paths.now()
    conn.execute(
        """UPDATE tests SET status = COALESCE(?, status), verdict = COALESCE(?, verdict),
           lesson = COALESCE(?, lesson),
           started = CASE WHEN ? = 'running' AND started IS NULL THEN ? ELSE started END,
           ended = CASE WHEN ? IN ('stopped','passed','failed') THEN ? ELSE ended END
           WHERE id = ?""",
        (status, verdict, lesson, status, stamp, status, stamp, row["id"]),
    )
    conn.commit()


def test_metric(conn, test_id, day, **values) -> None:
    tid = int(str(test_id).upper().lstrip("X"))
    _one(conn, "SELECT id FROM tests WHERE id = ?", (tid,))
    cols = ("spend", "impressions", "clicks", "add_to_cart", "checkouts", "purchases", "revenue", "source")
    data = {c: values.get(c) for c in cols}
    conn.execute(
        f"""INSERT INTO test_metrics (test_id, day, {', '.join(cols)}, recorded)
            VALUES (?, ?, {', '.join('?' * len(cols))}, ?)""",
        (tid, day, *[data[c] if data[c] is not None else (None if c == "source" else 0) for c in cols], paths.now()),
    )
    conn.commit()


def evaluate(conn, test_id) -> dict:
    """Aggregate a test's metrics. `sufficient` is False until minimum spend and clicks are met."""
    tid = int(str(test_id).upper().lstrip("X"))
    t = conn.execute(
        """SELECT COALESCE(SUM(spend),0) spend, COALESCE(SUM(impressions),0) impressions,
                  COALESCE(SUM(clicks),0) clicks, COALESCE(SUM(add_to_cart),0) atc,
                  COALESCE(SUM(checkouts),0) checkouts, COALESCE(SUM(purchases),0) purchases,
                  COALESCE(SUM(revenue),0) revenue, COUNT(*) days
           FROM test_metrics WHERE test_id = ?""", (tid,)).fetchone()
    div = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    cfg = paths.config()
    out = dict(t)
    out.update(
        cpm=div(t["spend"] * 1000, t["impressions"]), ctr=div(t["clicks"], t["impressions"]),
        cpc=div(t["spend"], t["clicks"]), cpa=div(t["spend"], t["purchases"]),
        roas=div(t["revenue"], t["spend"]), cvr=div(t["purchases"], t["clicks"]),
        atc_rate=div(t["atc"], t["clicks"]),
    )
    out["sufficient"] = t["spend"] >= cfg["min_spend_for_verdict_usd"] and t["clicks"] >= cfg["min_clicks_for_verdict"]
    return out


# ---------------------------------------------------------------- costs and logs

def month_spend(conn, when: datetime | None = None) -> float:
    month = (when or datetime.now()).strftime("%Y-%m")
    return conn.execute("SELECT COALESCE(SUM(amount_aud),0) FROM costs WHERE substr(ts,1,7) = ?",
                        (month,)).fetchone()[0]


def cost_check(conn, amount_aud: float) -> tuple[bool, float, float]:
    budget = paths.config()["monthly_ai_budget_aud"]
    spent = month_spend(conn)
    return spent + amount_aud <= budget, spent, budget


def cost_log(conn, service, amount_aud, *, task=None, note=None) -> int:
    cur = conn.execute("INSERT INTO costs (ts, service, amount_aud, task_id, note) VALUES (?,?,?,?,?)",
                       (paths.now(), service, amount_aud, parse_task_id(task) if task else None, note))
    conn.commit()
    return cur.lastrowid


def log_agent_run(conn, agent, session_id, event, note=None) -> None:
    conn.execute("INSERT INTO agent_runs (ts, agent, session_id, event, note) VALUES (?,?,?,?,?)",
                 (paths.now(), agent, session_id, event, note))
    conn.commit()


def log_action(conn, tool, summary, session_id=None) -> None:
    conn.execute("INSERT INTO actions (ts, tool, summary, session_id) VALUES (?,?,?,?)",
                 (paths.now(), tool, summary, session_id))
    conn.commit()
