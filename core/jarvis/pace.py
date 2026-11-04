"""Pacing background agent work against the Claude plan's weekly allowance (owner rule, 2026-11-05).

The owner wants nearly all of the weekly limit used, but never run out before the reset. So background
work (learning runs, sub-agent backlog) is paced: compare the usage so far with how far through the week
we are, speed up when behind, slow down when ahead, and always keep a reserve for urgent owner work.

Real usage comes from a Claude session (the `get_usage` tool shows the weekly and 5-hour windows), recorded
with `bin/jarvis pace record`. Between readings, each agent run since the last reading adds an estimated
cost (`pace_pct_per_run`, recalibrated from consecutive readings). With no reading this week, the whole
figure is an estimate and is labelled as one.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from . import db, paths

WEEK = timedelta(days=7)


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()


def reading(conn) -> dict | None:
    raw = db.get_meta(conn, "usage")
    return json.loads(raw) if raw else None


def _runs_since(conn, since: str) -> int:
    """Agent runs (every subagent start the hook logged) since a timestamp."""
    return conn.execute("SELECT COUNT(*) FROM agent_runs WHERE event = 'SubagentStart' AND ts > ?", (since,)).fetchone()[0]


def pct_per_run(conn) -> float:
    saved = db.get_meta(conn, "pace_pct_per_run")
    return float(saved) if saved else float(paths.config()["pace_pct_per_run"])


def record(conn, weekly: float, resets: str, *, five_hour=None, five_hour_resets=None, plan=None) -> dict:
    """Save a usage reading. Two readings in the same week recalibrate the cost of one agent run."""
    prev = reading(conn)
    now = paths.now()
    if prev and prev.get("resets") == resets and weekly > prev["weekly"]:
        runs = _runs_since(conn, prev["ts"])
        if runs:
            seen = (weekly - prev["weekly"]) / runs  # includes the owner's own use, so it errs high (safe)
            blended = 0.7 * pct_per_run(conn) + 0.3 * min(max(seen, 0.1), 5.0)
            db.set_meta(conn, "pace_pct_per_run", f"{blended:.3f}")
    data = {"ts": now, "weekly": float(weekly), "resets": _dt(resets).isoformat(timespec="seconds"),
            "five_hour": five_hour, "five_hour_resets": _dt(five_hour_resets).isoformat(timespec="seconds")
            if five_hour_resets else None, "plan": plan}
    db.set_meta(conn, "usage", json.dumps(data))
    return data


def status(conn, now: datetime | None = None) -> dict:
    """Where usage stands against an even pace to the weekly reset, and how many agent runs to start now."""
    cfg = paths.config()
    now = now or datetime.now().astimezone()
    per_run = pct_per_run(conn)
    ceiling = 100.0 - cfg["usage_reserve_pct"]
    batch = cfg["pace_max_batch"]
    r = reading(conn)
    out = {"per_run_pct": round(per_run, 3), "reserve_pct": cfg["usage_reserve_pct"], "ceiling_pct": ceiling}
    if r and _dt(r["resets"]) > now:
        runs = _runs_since(conn, r["ts"])
        used = r["weekly"] + runs * per_run
        resets = _dt(r["resets"])
        source = "reading" if runs == 0 else f"reading at {r['ts'][:16]} + estimate for {runs} run(s) since"
        five = r.get("five_hour") if r.get("five_hour_resets") and _dt(r["five_hour_resets"]) > now else None
    else:
        # No reading for this week: estimate from the runs logged in the last 7 days.
        since = (now - WEEK).isoformat(timespec="seconds")
        used = _runs_since(conn, since) * per_run
        resets = (r and _dt(r["resets"]) + WEEK * max(1, math.ceil((now - _dt(r["resets"])) / WEEK))) or now + WEEK
        source = "ESTIMATE (no usage reading this week: run get_usage and `bin/jarvis pace record`)"
        five = None
    step = timedelta(hours=cfg["pace_interval_hours"]) / WEEK
    if source.startswith("ESTIMATE"):
        # Rolling week: spend the ceiling evenly, one interval's share at a time.
        elapsed, target, target_next = None, used, min(ceiling, used + step * ceiling)
    else:
        elapsed = min(max((now - (resets - WEEK)) / WEEK, 0.0), 1.0)
        target = elapsed * ceiling
        target_next = min(ceiling, (elapsed + step) * ceiling)
    allowed = max(0, min(batch, math.floor((target_next - used) / per_run))) if per_run > 0 else 0
    reason = "behind pace: speed up" if used < target - 1 else "ahead of pace: slow down" if used > target + 1 \
        else "on pace"
    if used >= ceiling:
        allowed, reason = 0, f"reserve reached ({cfg['usage_reserve_pct']}% kept for urgent owner work)"
    elif five is not None and five >= cfg["usage_5h_ceiling_pct"]:
        allowed, reason = 0, f"5-hour window at {five}%: paused so the owner isn't blocked"
    out.update({"source": source, "estimate": source != "reading", "used_pct": round(used, 1),
                "target_pct": round(target, 1), "elapsed": None if elapsed is None else round(elapsed, 3), "resets": resets.isoformat(timespec="seconds"),
                "five_hour_pct": five, "allowed_runs": allowed, "status": reason})
    return out


def summary(conn) -> str:
    s = status(conn)
    return (f"Weekly usage {s['used_pct']}% vs even pace {s['target_pct']}% (ceiling {s['ceiling_pct']}%, "
            f"resets {s['resets'][:16]}). {s['status'].capitalize()}: start up to {s['allowed_runs']} background "
            f"run(s) now. Source: {s['source']}.")
