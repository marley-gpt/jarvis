"""Unattended routines: what JARVIS does on its own while the owner is away (T101).

One Windows scheduled task (`bin/jarvis routines install`) runs `routines tick` every 15 minutes, hidden
(pythonw). Each tick runs whatever is due:

- local routines, plain Python, no Claude usage: the sales watch, resuming stopped workers, social approvals,
  keeping the call server and JARVIS's phone line up (`call.keep_line`), rewriting brain/ESSENTIALS.md and
  pushing the lean brain to OneDrive (`brain-sync`, see sync.py), retrying reels Instagram refused (`reels-retry`,
  see media.py); on a standby the tick only pulls that brain;
- Claude routines (morning brief, evening summary, nightly learning, skill learning) are the Claude desktop
  app's scheduled tasks (~/.claude/scheduled-tasks/<name>/SKILL.md). Those only fire while the desktop app is
  open, so if one hasn't run by its time plus a grace period, the tick starts the same prompt as a durable
  worker (`bin/jarvis work`), which needs no window and survives restarts. A routine counts as done when its
  report file exists, a worker for it ran today, or something ran `bin/jarvis routines mark <name>`.
  Learning runs are paced (`bin/jarvis pace`); the briefs aren't, they're for the owner.

    bin/jarvis routines                status line and each routine's last run and next one
    bin/jarvis routines tick           run what's due (the scheduler calls this)
    bin/jarvis routines run <name>     run one now
    bin/jarvis routines mark <name>    a desktop scheduled task did it today
    bin/jarvis routines install | uninstall

Per-routine overrides in brain/config.json: {"routines": {"nightly-learning": {"at": "22:00"}, "social-sync":
{"enabled": false}}}.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import paths, records

TASK_NAME = "JARVIS routines"
TICK_MINUTES = 15
SCHEDULED = Path.home() / ".claude" / "scheduled-tasks"

ROUTINES = [
    {"name": "sales-watch", "kind": "local", "every": 15, "what": "Shopify orders and Meta spend into the ledger, sale and kill-rule alerts"},
    {"name": "worker-resume", "kind": "local", "every": 30, "what": "restart workers that stopped mid-task"},
    {"name": "social-sync", "kind": "local", "every": 60, "what": "carry the owner's approve/reject onto social drafts"},
    {"name": "phone-line", "kind": "local", "every": 15, "what": "call server up and JARVIS's phone line signed in, so the owner can ring him"},
    {"name": "brain-sync", "kind": "local", "every": 120, "what": "rewrite brain/ESSENTIALS.md and push the lean brain to OneDrive for the laptop"},
    {"name": "reels-retry", "kind": "local", "every": 15, "what": "retry reels Instagram wouldn't download (brain/work/reels-pending.txt)"},
    {"name": "morning-brief", "kind": "claude", "at": "08:00", "grace": 45, "window": 4, "task": "jarvis-morning-brief",
     "done_file": "reports/{day}-morning.md", "what": "8am brief to the owner's phone"},
    {"name": "evening-summary", "kind": "claude", "at": "21:00", "grace": 45, "window": 2.5, "task": "jarvis-evening-summary",
     "done_file": "reports/{day}-evening.md", "what": "9pm summary to the owner's phone"},
    {"name": "nightly-learning", "kind": "claude", "at": "22:30", "grace": 30, "window": 1.4, "task": "jarvis-daily-learning",
     "paced": True, "what": "watch one useful ecom video and save the lessons"},
    {"name": "skill-learning", "kind": "claude", "at": "01:30", "grace": 30, "window": 4, "task": "jarvis-skill-learning",
     "paced": True, "what": "the agents' skill-learning run"},
]


class RoutineError(records.RecordError):
    pass


def folder() -> Path:
    d = paths.work_dir() / "routines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state() -> dict:
    try:
        return json.loads((folder() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    tmp = folder() / "state.json.tmp"
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(folder() / "state.json")


def routines() -> list[dict]:
    over = paths.config().get("routines") or {}
    return [{**r, "enabled": True, **(over.get(r["name"]) or {})} for r in ROUTINES]


def get(name: str) -> dict:
    for r in routines():
        if r["name"] == name:
            return r
    raise RoutineError(f"No routine '{name}'. Routines: {', '.join(r['name'] for r in ROUTINES)}")


def _log(entry: dict) -> None:
    with open(folder() / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _at(r: dict, now: datetime) -> datetime:
    h, m = (int(x) for x in r["at"].split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def done_today(r: dict, st: dict, now: datetime) -> str | None:
    """Why a daily Claude routine counts as done today, or None."""
    day = now.date().isoformat()
    s = st.get(r["name"]) or {}
    if s.get("marked") == day:
        return "marked done"
    if s.get("started_day") == day:
        return f"fallback worker W{s.get('worker')} started"
    if r.get("done_file") and (paths.home() / r["done_file"].format(day=day)).exists():
        return "report written"
    return None


def due(r: dict, st: dict, now: datetime) -> bool:
    if not r["enabled"]:
        return False
    s = st.get(r["name"]) or {}
    if r["kind"] == "local":
        last = s.get("last_run")
        return not last or now - datetime.fromisoformat(last) >= timedelta(minutes=r["every"] - 1)
    start = _at(r, now) + timedelta(minutes=r.get("grace", 0))
    return start <= now < start + timedelta(hours=r.get("window", 3)) and not done_today(r, st, now)


def next_run(r: dict, st: dict, now: datetime) -> datetime | None:
    if not r["enabled"]:
        return None
    if r["kind"] == "local":
        last = (st.get(r["name"]) or {}).get("last_run")
        return max(now, datetime.fromisoformat(last) + timedelta(minutes=r["every"])) if last else now
    at = _at(r, now)
    if at + timedelta(minutes=r.get("grace", 0), hours=r.get("window", 3)) <= now or done_today(r, st, now):
        at += timedelta(days=1)
    return at


def _when(t: datetime | None, now: datetime) -> str:
    if t is None:
        return "off"
    if t <= now:
        return "now"
    return t.strftime("%H:%M" if t.date() == now.date() else "%a %H:%M")


def _prompt(r: dict) -> str:
    path = SCHEDULED / r["task"] / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutineError(f"The prompt for {r['name']} isn't there ({path}).") from exc
    if text.startswith("---"):
        text = text.split("---", 2)[-1]
    return (text.strip() + f"\n\nThis is an unattended run started by `bin/jarvis routines` because the desktop "
            f"scheduled task '{r['task']}' had not run by {r['at']} plus {r.get('grace', 0)} minutes. The owner may "
            f"be away from the PC: be quick, never take an action behind an approval gate. When finished run "
            f"`bin/jarvis routines mark {r['name']}`.")


def _run_local(conn, name: str, now: datetime) -> str:
    if name == "sales-watch":
        from . import sales_watch
        rep = sales_watch.watch(conn, now=now.astimezone())
        bits = [f"via {rep['source']}", f"{rep['new_orders']} new order(s)", f"{len(rep['alerts'])} alert(s)"]
        return "; ".join(bits + rep["errors"])
    if name == "worker-resume":
        from . import work
        resumed = work.resume()
        return f"resumed {', '.join('W%d' % w['id'] for w in resumed)}" if resumed else "nothing stopped"
    if name == "social-sync":
        from . import social
        changed = social.sync(conn)
        return f"{len(changed)} decision(s) picked up" if changed else "no new decisions"
    if name == "phone-line":
        from . import call
        return call.keep_line()
    if name == "brain-sync":
        from . import essentials, sync
        essentials.write(conn)
        return sync.push(conn)["note"]
    if name == "reels-retry":
        from . import media
        if not media.load_pending():
            return "nothing waiting"
        rep = media.retry_pending(conn, now=now)
        return f"{len(rep['watched'])} watched, {rep['waiting']} waiting" + (f", {len(rep['gave_up'])} given up" if rep["gave_up"] else "")
    raise RoutineError(f"No local routine '{name}'.")


def _run_claude(conn, r: dict, now: datetime, start_worker) -> tuple[bool, str, int | None]:
    if r.get("paced"):
        from . import pace
        p = pace.status(conn)
        if p["allowed_runs"] <= 0:
            return False, f"paused by the usage pacer ({p['status']})", None
    run = start_worker(_prompt(r))
    records.log_agent_run(conn, "jarvis", run.get("session"), "SubagentStart", note=f"routine {r['name']} W{run['id']}")
    return True, f"started worker W{run['id']}", run["id"]


def _default_worker(brief: str) -> dict:
    from . import work
    return work.start(brief, task="", agent="")


def run_one(conn, r: dict, st: dict, now: datetime, start_worker=_default_worker) -> dict:
    s = st.setdefault(r["name"], {})
    t0 = time.time()
    try:
        if r["kind"] == "local":
            ok, note, wid = True, _run_local(conn, r["name"], now), None
        else:
            ok, note, wid = _run_claude(conn, r, now, start_worker)
            if wid:
                s.update(started_day=now.date().isoformat(), worker=wid)
    except Exception as exc:  # one broken routine must not stop the others
        ok, note, wid = False, f"failed: {exc}"[:300], None
    s.update(last_run=now.isoformat(timespec="seconds"), ok=ok, note=note)
    entry = {"ts": paths.now(), "routine": r["name"], "ok": ok, "note": note, "secs": round(time.time() - t0, 1)}
    _log(entry)
    return entry


def tick(conn, now: datetime | None = None, start_worker=_default_worker) -> list[dict]:
    if paths.standby():
        return standby_pull(conn)  # the primary runs the routines; a standby only keeps its brain copy fresh (T105)
    now = now or datetime.now()
    lock = folder() / "tick.lock"
    try:
        if time.time() - lock.stat().st_mtime < 600:
            return []  # the previous tick is still going
    except OSError:
        pass
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        st = load_state()
        done = [run_one(conn, r, st, now, start_worker) for r in routines() if due(r, st, now)]
        st["_tick"] = now.isoformat(timespec="seconds")
        save_state(st)
        return done
    finally:
        lock.unlink(missing_ok=True)


def standby_pull(conn) -> list[dict]:
    """On the standby: pull the brain the primary pushed, if it's newer. Quiet when there's nothing new."""
    from . import sync
    try:
        res = sync.pull(conn)
        if not res["pulled"]:
            return []
        ok, note = True, res["note"]
    except Exception as exc:
        ok, note = False, f"failed: {exc}"[:300]
    entry = {"ts": paths.now(), "routine": "brain-pull", "ok": ok, "note": note}
    _log(entry)
    return [entry]


def run_now(conn, name: str, start_worker=_default_worker) -> dict:
    st = load_state()
    entry = run_one(conn, get(name), st, datetime.now(), start_worker)
    save_state(st)
    return entry


def mark(name: str, now: datetime | None = None) -> None:
    get(name)
    st = load_state()
    st.setdefault(name, {})["marked"] = (now or datetime.now()).date().isoformat()
    save_state(st)


# ---------------------------------------------------------------- the Windows scheduled task

def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def command() -> list[str]:
    return [_pythonw(), str(paths.CORE_DIR / "jarvis_cli.py"), "routines", "tick"]


def installed() -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=paths.NO_WINDOW)
    return r.returncode == 0


def install() -> str:
    tr = " ".join(f'"{c}"' if " " in c or "\\" in c else c for c in command())
    if os.name != "nt":
        return f"Add this line to crontab -e:\n*/{TICK_MINUTES} * * * * {tr}"
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(TICK_MINUTES), "/TN", TASK_NAME,
                        "/TR", tr], capture_output=True, text=True, creationflags=paths.NO_WINDOW)
    if r.returncode != 0:
        raise RoutineError(f"schtasks: {(r.stderr or r.stdout).strip()}")
    return f"Scheduled task '{TASK_NAME}' runs every {TICK_MINUTES} minutes while you're signed in: {tr}"


def uninstall() -> str:
    if os.name != "nt":
        return "Remove the routines line from crontab -e."
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=paths.NO_WINDOW)
    return "Removed." if r.returncode == 0 else f"Nothing removed: {(r.stderr or r.stdout).strip()}"


# ---------------------------------------------------------------- status

def status(now: datetime | None = None, check_scheduler: bool = True) -> dict:
    now = now or datetime.now()
    st = load_state()
    last_tick = st.get("_tick")
    age = (now - datetime.fromisoformat(last_tick)).total_seconds() / 60 if last_tick else None
    rows = []
    for r in routines():
        s = st.get(r["name"]) or {}
        nxt = next_run(r, st, now)
        rows.append({"name": r["name"], "kind": r["kind"], "what": r["what"], "enabled": r["enabled"],
                     "schedule": f"every {r['every']} min" if r["kind"] == "local" else f"daily {r['at']}",
                     "last_run": s.get("last_run"), "ok": s.get("ok"), "note": s.get("note"),
                     "done_today": done_today(r, st, now) if r["kind"] == "claude" else None,
                     "next": _when(nxt, now), "next_at": nxt.isoformat(timespec="minutes") if nxt else None})
    on = age is not None and age <= TICK_MINUTES * 2 + 1
    return {"on": on, "scheduler": installed() if check_scheduler else None, "last_tick": last_tick,
            "minutes_since_tick": None if age is None else round(age), "routines": rows}


def status_line(s: dict | None = None) -> str:
    s = s or status()
    if s["on"]:
        head = f"Routines on (last tick {s['minutes_since_tick']} min ago)"
    elif s["last_tick"]:
        head = f"Routines STALLED (last tick {s['minutes_since_tick']} min ago)"
    else:
        head = "Routines off" + ("" if s.get("scheduler") else ": run `bin/jarvis routines install`")
    bad = [r["name"] for r in s["routines"] if r["ok"] is False]
    upcoming = sorted((r for r in s["routines"] if r["kind"] == "claude" and r["enabled"]), key=lambda r: r["next_at"] or "~")
    bits = [head]
    if bad:
        bits.append("failing: " + ", ".join(bad))
    done = [r["name"] for r in s["routines"] if r.get("done_today")]
    if done:
        bits.append("done today: " + ", ".join(done))
    if upcoming:
        bits.append("next: " + ", ".join(f"{r['name']} {r['next']}" for r in upcoming[:2]))
    return " · ".join(bits)
