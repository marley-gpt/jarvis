"""The PC and the laptop as one team (T121): heartbeats, automatic phone failover and shared work.

Both machines keep a small folder in the brain-sync folder in OneDrive (next to phone.json and the lean brain):

    fleet/heartbeat-<machine>.json   written every 20 s by the JARVIS app (and every routines tick): is this
                                     machine up, is its app open, is it answering the phone, how busy is it
    fleet/jobs/S-<...>.json          shared jobs: a worker brief either machine may run
    fleet/jobs/S-<...>.lock          who is running that job now (one machine, refreshed while it runs)

Failover (the rule, so it's never a guess):
  1. The machine named in phone.json answers. If its heartbeat hasn't said "answering" for STALE seconds
     (3 minutes), the other machine takes the phone by itself: it pulls the last brain pushed, names itself in
     phone.json (marked automatic, remembering the machine it took over from), opens the JARVIS app if it's shut
     so Telegram starts, and tells the owner.
  2. It hands back by itself only after an automatic takeover, and only once the machine it took over from has
     been up with its app open for HANDBACK_AFTER seconds (5 minutes), so a flicker doesn't bounce the phone.
     A machine the owner chose (/laptop, /desktop, bin/jarvis phone) is never handed away automatically.
     `failover.handback: "ask"` in brain/config.json turns automatic hand-back off: then only /desktop does it.
  3. Never both: if Telegram says another machine is polling (409 Conflict) while this one holds the phone by an
     automatic takeover, this one steps back to the machine it took over from. A machine whose OneDrive was
     slow to bring the note (just switched on) can't answer alongside the one that really holds it for long.
  `failover.auto: false` in brain/config.json switches the automatic takeover off.

Before a machine that answered steps back, its own unsent work is safe: while it is the primary it keeps a copy of
the last brain it pushed (outside brain/), and once it is a standby that copy becomes its baseline for the T119
outbox (twoway.py), so the first pull after a takeover keeps everything it did since its last push.

Shared work: `bin/jarvis work start --anywhere ...` queues the brief as a shared job. The machine answering the
phone hands each job to the least busy machine that is up (by heartbeat); that machine takes the lock and starts
an ordinary worker. If a machine running a job goes quiet for LOCK_STALE seconds, the job is handed to another
machine, which starts it again with a note to carry on from what was saved; if the quiet one comes back still
running it, it sees the lock is no longer its own and stops its copy. A job is never started twice at once.

    bin/jarvis fleet               the machines, who answers, the failover rule and the shared jobs
    bin/jarvis fleet tick          one round now (the app and the routines tick do this by themselves)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths

STALE = 180          # seconds without "answering" before the other machine takes the phone
HANDBACK_AFTER = 300  # seconds the original machine must be back before an automatic hand-back
LOCK_STALE = 900     # seconds without a heartbeat before a shared job is given to another machine
TICK_WRITE = 60      # the routines tick leaves the heartbeat alone when the app wrote one this recently
CONFLICTS_TO_YIELD = 3
MAX_JOB_ATTEMPTS = 3
DASHBOARD_PORT = 8765
JOB_NAME = re.compile(r"^S-\d{6}-\d{6}-[0-9a-f]{4}$")
CARRY_ON = ("This shared job was started on {was}, which went quiet before it finished, so it has moved to this "
            "machine. Check what is already saved (the task's deliverable path, bin/jarvis task show, recent memory) "
            "and carry on from there rather than starting over.\n\n")


class FleetError(Exception):
    pass


def here() -> str:
    from . import phone
    return phone.this_machine()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "unknown")


def fleet_dir() -> Path:
    from . import sync
    return sync.sync_dir() / "fleet"


def jobs_dir() -> Path:
    return fleet_dir() / "jobs"


def _settings() -> dict:
    cfg = paths.config().get("failover") or {}
    return {"auto": cfg.get("auto", True), "handback": cfg.get("handback", "auto"),
            "stale": float(cfg.get("stale_seconds", STALE)), "handback_after": float(cfg.get("handback_after", HANDBACK_AFTER)),
            "lock_stale": float(cfg.get("lock_stale", LOCK_STALE))}


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)  # OneDrive busy: the next round writes it again
        return False


# ---------------------------------------------------------------- local state (outside brain/, never synced)

def _local_path() -> Path:
    return paths.role_file().with_name("fleet-state.json")


def _local() -> dict:
    return _read(_local_path())


def _save_local(data: dict) -> None:
    _write(_local_path(), data)


# ---------------------------------------------------------------- heartbeats

def heartbeat_path(name: str | None = None) -> Path:
    return fleet_dir() / f"heartbeat-{_safe(name or here())}.json"


def dashboard_up(port: int = DASHBOARD_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _can_answer() -> bool:
    from . import telegram
    try:
        return bool(telegram.settings().get("bot_token"))
    except Exception:
        return False


def _running_workers() -> int:
    from . import work
    try:
        return sum(1 for r in work.listing(60) if r["status"] == "running")
    except Exception:
        return 0


def beat(bridge=None, now: float | None = None) -> dict | None:
    """Write this machine's heartbeat. `bridge` is the app's Telegram bridge; without it (the routines tick) the
    heartbeat is left alone when the app wrote one a moment ago, because the app knows more."""
    now = time.time() if now is None else now
    path = heartbeat_path()
    old = _read(path)
    if bridge is None and old.get("app") and now - float(old.get("ts") or 0) < TICK_WRITE:
        return None
    app = bridge is not None or dashboard_up()
    answering = bool(bridge is not None and getattr(bridge, "running", False))
    fresh = now - float(old.get("ts") or 0) <= _settings()["stale"]
    hb = {
        "machine": here(), "ts": now, "stamp": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "role": paths.machine_role(), "app": app, "answering": answering, "can_answer": _can_answer(),
        "workers": _running_workers(),
        # up_since: the start of this unbroken run of heartbeats; app_since: of the app being open in it
        "up_since": old.get("up_since") if fresh and old.get("up_since") else now,
        "app_since": (old.get("app_since") or now) if app and fresh and old.get("app") else (now if app else None),
        "answering_ts": now if answering else old.get("answering_ts"),
    }
    _write(path, hb)
    return hb


def heartbeats(now: float | None = None) -> list[dict]:
    """Every machine's last heartbeat, with its age in seconds."""
    now = time.time() if now is None else now
    out = []
    d = fleet_dir()
    for p in sorted(d.glob("heartbeat-*.json")) if d.exists() else []:
        hb = _read(p)
        if not hb.get("machine") or not p.stem.endswith(_safe(hb["machine"])):
            continue  # an OneDrive conflict copy
        hb["age"] = now - float(hb.get("ts") or 0)
        out.append(hb)
    return out


def heartbeat_of(name: str, now: float | None = None) -> dict | None:
    return next((h for h in heartbeats(now) if h["machine"].lower() == (name or "").lower()), None)


def _alive(hb: dict | None, stale: float) -> bool:
    return bool(hb) and hb["age"] <= stale


def silent_for(hb: dict, now: float) -> float:
    """How long the machine has not been answering the phone."""
    return now - float(hb.get("answering_ts") or hb.get("up_since") or hb.get("ts") or 0)


# ---------------------------------------------------------------- failover

def _notify(text: str) -> None:
    # Owner (2026-11-13): takeover and hand-back notices are noise; they stay in the fleet log unless
    # failover.notify is set true in brain/config.json.
    if not (paths.config().get("failover") or {}).get("notify", False):
        return
    from . import telegram
    try:
        telegram.send(text)
    except Exception:
        pass  # not paired, or offline: the fleet log still has it


def _log(entry: dict) -> None:
    d = paths.work_dir() / "routines"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "fleet.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": paths.now(), "machine": here(), **entry}) + "\n")


def _start_app() -> None:
    """Open the JARVIS app here, without a window or a browser, so the Telegram bridge starts."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    log = paths.scratch_dir() / "dashboard-failover.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = (paths.NO_WINDOW | 0x00000008 | 0x00000200) if os.name == "nt" else 0
    with open(log, "ab") as out:
        subprocess.Popen([exe, str(paths.CORE_DIR / "jarvis_cli.py"), "dashboard", "--no-open"], cwd=paths.REPO_ROOT,
                         stdout=out, stderr=out, stdin=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                         start_new_session=os.name != "nt")


def failover(conn, bridge=None, now: float | None = None, start_app=_start_app) -> str | None:
    """Take the phone when the machine holding it has gone quiet, hand it back by the rule, step back on a
    Telegram conflict. Returns what it did, or None."""
    from . import phone, sync
    now = time.time() if now is None else now
    cfg = _settings()
    holder = phone.holder()
    me = here()
    if not holder:
        # Nobody named yet: the role files decide. The primary names itself, once no other machine claims to be
        # the primary, so the standby has a heartbeat to watch.
        others = [h for h in heartbeats(now) if h["machine"].lower() != me.lower() and _alive(h, cfg["stale"])
                  and h.get("role") == "primary"]
        if paths.machine_role() != "primary" or others:
            return None
        phone.hand_to(conn, "here")
        _log({"event": "named", "note": f"{me} named itself in phone.json (it was the primary by its role file)"})
        return f"{me} answers the phone (named in phone.json)"
    note = phone.state()
    local = _local()

    if holder.lower() == me.lower():
        home = note.get("home")
        if not (note.get("auto") and home and home.lower() != me.lower()):
            if local.pop("conflicts", None) is not None:
                _save_local(local)
            return None
        # Never both: Telegram refuses a second poller with 409 Conflict. The automatic holder is the one to yield.
        err = str(getattr(bridge, "error", "") or "").lower() if bridge is not None else ""
        conflicts = local.get("conflicts", 0) + 1 if ("conflict" in err or "409" in err) else 0
        local["conflicts"] = conflicts
        _save_local(local)
        if conflicts >= CONFLICTS_TO_YIELD:
            local.update(conflicts=0, yielded_until=now + 2 * cfg["stale"])
            _save_local(local)
            phone.hand_to(conn, home)
            msg = f"{home} is answering Telegram too, so {me} stepped back and {home} keeps your phone."
            _log({"event": "yield", "to": home, "note": msg})
            return msg
        back = heartbeat_of(home, now)
        if cfg["handback"] != "auto" or not _alive(back, cfg["stale"]) or not back.get("app") \
                or not back.get("can_answer"):
            return None
        if now - float(back.get("app_since") or now) < cfg["handback_after"]:
            return None
        phone.hand_to(conn, home)  # pushes this machine's brain first, then names home
        msg = f"{home} is back, so I've handed your phone back to it, sir. {me} is the standby again."
        _log({"event": "handback", "to": home, "note": msg})
        _notify(msg)
        return msg

    # Another machine holds the phone. Is it still answering?
    if not cfg["auto"] or not _can_answer():
        return None
    if now < float(local.get("yielded_until") or 0):
        return None
    hb = heartbeat_of(holder, now)
    if hb is None:
        return None  # it has never written a heartbeat (older JARVIS there): don't guess, the owner switches
    quiet = silent_for(hb, now)
    if quiet <= cfg["stale"]:
        return None
    # Only one taker: of the machines that are up and could answer, the first by name.
    takers = sorted([me] + [h["machine"] for h in heartbeats(now) if h["machine"].lower() not in (me.lower(), holder.lower())
                            and _alive(h, cfg["stale"]) and h.get("can_answer")], key=str.lower)
    if takers[0].lower() != me.lower():
        return None
    try:
        pulled = sync.pull(conn, force=True)["note"]
    except Exception as exc:  # answering matters more than a fresh copy; say so
        pulled = f"couldn't pull the brain ({exc})"
    phone.hand_to(conn, "here", takeover=True, auto_from=holder)
    if not dashboard_up():
        start_app()
    mins = int(quiet // 60)
    msg = (f"{holder} has been quiet for {mins} minute{'s' if mins != 1 else ''}, so {me} has taken your phone, "
           f"sir. It hands back by itself once {holder} has been back for {int(cfg['handback_after'] // 60)} minutes"
           + (", or text /desktop." if cfg["handback"] == "auto" else "; text /desktop when you want it back."))
    _log({"event": "takeover", "from": holder, "quiet_s": round(quiet), "pull": pulled, "note": msg})
    _notify(msg)
    return msg


# ---------------------------------------------------------------- a returning machine keeps its own work

def _own_push_path() -> Path:
    return paths.role_file().with_name("own-push.zip")


def keep_own_push() -> bool:
    """On the primary: keep a copy of the last brain this machine pushed, the baseline it needs if it is ever
    taken over while away (see the module note)."""
    from . import sync
    m = sync.manifest()
    if (m.get("machine") or "").lower() != here().lower():
        return False
    local = _local()
    if local.get("own_push_stamp") == m.get("stamp") and _own_push_path().exists():
        return False
    src = sync.sync_dir() / sync.BRAIN_ZIP
    dest = _own_push_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        shutil.copyfile(src, tmp)
        with zipfile.ZipFile(tmp) as zf:
            if "jarvis.db" not in zf.namelist():
                raise zipfile.BadZipFile("no database")
        os.replace(tmp, dest)
    except (OSError, zipfile.BadZipFile):
        tmp.unlink(missing_ok=True)
        return False
    local.update(own_push_stamp=m.get("stamp"), own_push_fp=m.get("fingerprint"))
    _save_local(local)
    return True


def baseline_from_own_push() -> bool:
    """On a standby that used to answer: if its last own push is newer than its pull baseline, make that push the
    baseline, so the T119 outbox carries everything it did after it, and the next pull loses none of it."""
    from . import twoway
    local = _local()
    src = _own_push_path()
    stamp = local.get("own_push_stamp")
    if not stamp or not src.exists():
        return False
    try:
        pushed_at = datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return False
    base = _read(twoway._baseline_json())
    if base.get("exact") and float(base.get("at") or 0) >= pushed_at:
        return False
    import tempfile
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(src) as zf:
        zf.extract("jarvis.db", tmp)
        twoway.save_baseline(Path(tmp) / "jarvis.db", zf, stamp, pushed_at)
    from . import sync
    st = sync.local_state()
    if not st.get("pulled_at"):
        # A machine that never pulled: from now on it counts its own push as its last pull.
        sync._save_local_state({**st, "pulled_stamp": stamp, "pulled_fp": local.get("own_push_fp"),
                                "pulled_at": stamp, "from": here()})
    _log({"event": "baseline", "note": f"own push {stamp} is the outbox baseline"})
    return True


# ---------------------------------------------------------------- shared jobs

def _job_path(jid: str) -> Path:
    return jobs_dir() / f"{jid}.json"


def _lock_path(jid: str) -> Path:
    return jobs_dir() / f"{jid}.lock"


def jobs() -> list[dict]:
    d = jobs_dir()
    out = [_read(p) for p in sorted(d.glob("S-*.json"))] if d.exists() else []
    return [j for j in out if JOB_NAME.match(str(j.get("id") or ""))]


def get_job(jid: str) -> dict:
    j = _read(_job_path(jid))
    if not j:
        raise FleetError(f"No shared job {jid}.")
    return j


def _save_job(job: dict) -> None:
    job["updated"] = paths.now()
    _write(_job_path(job["id"]), job)


def share(brief: str, task: str = "", agent: str = "", model: str = "") -> dict:
    """Queue a worker brief for whichever machine is up and least busy."""
    brief = (brief or "").strip()
    if not brief:
        raise FleetError("A shared job needs a brief.")
    jid = f"S-{datetime.now().strftime('%y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    job = {"id": jid, "brief": brief, "task": task, "agent": agent, "model": model, "by": here(),
           "created": paths.now(), "status": "queued", "machine": None, "worker": None, "attempts": 0, "history": []}
    _save_job(job)
    return job


def lock_of(jid: str) -> dict:
    return _read(_lock_path(jid))


def _take_lock(jid: str, now: float) -> bool:
    """The lock is this machine's, or free. Created exclusively, so two processes here can't both take it."""
    path = _lock_path(jid)
    held = _read(path)
    if held and held.get("machine", "").lower() != here().lower():
        return False
    if not held:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"machine": here(), "ts": now, "pid": os.getpid()}, f)
    return True


def _touch_lock(jid: str, now: float, worker: str | None = None) -> None:
    held = lock_of(jid)
    _write(_lock_path(jid), {**held, "machine": here(), "ts": now, "worker": worker or held.get("worker")})


def dispatch(now: float | None = None) -> list[str]:
    """On the machine answering the phone: give each queued job to the least busy machine that is up, and move a
    job off a machine that has gone quiet."""
    now = time.time() if now is None else now
    cfg = _settings()
    beats = {h["machine"].lower(): h for h in heartbeats(now)}
    me = here()
    up = [me] + [h["machine"] for k, h in beats.items() if k != me.lower() and _alive(h, cfg["stale"])]

    def load(name: str) -> int:
        h = beats.get(name.lower()) or {}
        mine = sum(1 for j in jobs() if (j.get("machine") or "").lower() == name.lower()
                   and j["status"] == "assigned")  # running ones are already in the heartbeat's worker count
        return int(h.get("workers") or 0) + mine

    notes = []
    for job in jobs():
        if job["status"] in ("assigned", "running") and job.get("machine"):
            hb = beats.get(job["machine"].lower())
            lock = lock_of(job["id"])
            last = max(float((hb or {}).get("ts") or 0), float(lock.get("ts") or 0))
            if job["machine"].lower() == me.lower() or now - last <= cfg["lock_stale"]:
                continue
            if job.get("attempts", 0) >= MAX_JOB_ATTEMPTS:
                job["status"] = "failed"
                job["history"].append({"ts": paths.now(), "event": f"gave up: {job['machine']} went quiet"})
                _save_job(job)
                notes.append(f"{job['id']} failed: {job['machine']} went quiet {MAX_JOB_ATTEMPTS} times")
                continue
            job["history"].append({"ts": paths.now(), "event": f"{job['machine']} went quiet; moving it"})
            job.update(was=job["machine"], status="queued", machine=None)
            _lock_path(job["id"]).unlink(missing_ok=True)
        if job["status"] != "queued":
            continue
        target = min(up, key=lambda n: (load(n), n.lower() != me.lower(), n.lower()))
        job.update(status="assigned", machine=target, assigned_at=paths.now())
        job["history"].append({"ts": paths.now(), "event": f"assigned to {target} by {me}"})
        _save_job(job)
        notes.append(f"{job['id']} → {target}")
    return notes


def run_mine(now: float | None = None, start=None) -> list[str]:
    """On every machine: start the shared jobs assigned here, keep their locks fresh, record how they ended, and
    stop a local copy whose job has moved to another machine."""
    from . import work
    now = time.time() if now is None else now
    start = start or work.start
    me = here().lower()
    local = _local()
    started = dict(local.get("jobs") or {})  # shared job id -> the worker this machine started for it
    notes = []
    for job in jobs():
        mine = (job.get("machine") or "").lower() == me
        wid_here = started.get(job["id"])
        if wid_here:
            current = (mine and job["status"] == "running" and job.get("worker") == wid_here
                       and (job.get("worker_machine") or "").lower() == me
                       and (lock_of(job["id"]).get("machine") or here()).lower() == me)
            if not current and job["status"] != "done":
                # Cancelled, or moved to another machine while this one was quiet: never let both run it.
                try:
                    if work.refresh(work.get(wid_here))["status"] == "running":
                        work.stop(wid_here)
                        where = "it was cancelled" if job["status"] == "cancelled" else f"it moved to {job.get('machine')}"
                        notes.append(f"{job['id']}: stopped {wid_here} here, {where}")
                except work.WorkError:
                    pass
                started.pop(job["id"], None)
                wid_here = None
        if not mine:
            continue
        if job["status"] == "assigned":
            if not _take_lock(job["id"], now):
                continue
            brief = (CARRY_ON.format(was=job["was"]) if job.get("was") else "") + job["brief"]
            try:
                run = start(brief, task=job.get("task") or "", agent=job.get("agent") or "", model=job.get("model") or "")
            except Exception as exc:
                job["history"].append({"ts": paths.now(), "event": f"couldn't start on {here()}: {exc}"[:300]})
                job.update(status="queued", machine=None)
                _lock_path(job["id"]).unlink(missing_ok=True)
                _save_job(job)
                notes.append(f"{job['id']}: couldn't start here ({exc})")
                continue
            wid = f"W{run['id']}"
            job.update(status="running", worker=wid, worker_machine=here(), attempts=job.get("attempts", 0) + 1)
            job["history"].append({"ts": paths.now(), "event": f"started on {here()} as {wid}"})
            _save_job(job)
            _touch_lock(job["id"], now, wid)
            started[job["id"]] = wid
            notes.append(f"{job['id']}: started {wid} here")
        elif job["status"] == "running" and wid_here:
            local_id = wid_here
            try:
                run = work.refresh(work.get(local_id))
            except work.WorkError:
                continue
            if run["status"] in ("running", "stopped", "new"):  # stopped: worker-resume restarts it here
                _touch_lock(job["id"], now, local_id)
                continue
            job["status"] = "done" if run["status"] == "done" else "failed"
            job["history"].append({"ts": paths.now(), "event": f"{local_id} {run['status']} on {here()}"})
            _save_job(job)
            _lock_path(job["id"]).unlink(missing_ok=True)
            started.pop(job["id"], None)
            notes.append(f"{job['id']}: {job['status']}")
    if started != (local.get("jobs") or {}):
        local = _local()  # failover may have written it meanwhile
        local["jobs"] = started
        _save_local(local)
    return notes


def cancel(jid: str) -> dict:
    job = get_job(jid)
    if job["status"] in ("done", "failed", "cancelled"):
        raise FleetError(f"{jid} is already {job['status']}.")
    from . import work
    if job.get("worker") and (job.get("worker_machine") or "").lower() == here().lower():
        try:
            work.stop(job["worker"])
        except work.WorkError:
            pass
    job["status"] = "cancelled"
    job["history"].append({"ts": paths.now(), "event": f"cancelled on {here()}"})
    _save_job(job)
    _lock_path(jid).unlink(missing_ok=True)  # the other machine stops its copy when it sees the job cancelled
    return job


# ---------------------------------------------------------------- one round

def _step_lock() -> Path:
    return paths.role_file().with_name("fleet-step.lock")


def step(conn, bridge=None, now: float | None = None, start_app=_start_app, start_worker=None) -> list[str]:
    """One round: heartbeat, keep this machine's own work safe, failover, shared jobs. Called every 20 s by the
    JARVIS app and every routines tick; a second caller on the same machine skips the round."""
    now = time.time() if now is None else now
    lock = _step_lock()
    try:
        if time.time() - lock.stat().st_mtime < 120:
            return []
    except OSError:
        pass
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        return []
    notes: list[str] = []
    try:
        beat(bridge, now)
        try:
            if paths.machine_role() == "primary":
                keep_own_push()
            else:
                baseline_from_own_push()
        except Exception as exc:
            notes.append(f"own-work baseline: {type(exc).__name__}: {exc}")
        did = failover(conn, bridge, now, start_app=start_app)
        if did:
            notes.append(did)
            beat(bridge, now)  # the other machine sees the new role at once
        from . import phone
        if (phone.holder() or "").lower() == here().lower():
            notes += dispatch(now)
        notes += run_mine(now, start=start_worker)
    finally:
        lock.unlink(missing_ok=True)
    return notes


# ---------------------------------------------------------------- status

def _ago(secs: float) -> str:
    secs = max(0, secs)
    if secs < 90:
        return f"{int(secs)} s ago"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    return f"{secs / 3600:.1f} h ago"


def lines(now: float | None = None) -> list[str]:
    from . import phone
    now = time.time() if now is None else now
    cfg = _settings()
    note = phone.state()
    holder = phone.holder()
    out = [phone.status_line()]
    if note.get("auto") and note.get("home"):
        out.append(f"Taken over automatically from {note['home']}; "
                   + (f"hands back once {note['home']} has been back {int(cfg['handback_after'] // 60)} min."
                      if cfg["handback"] == "auto" else "hands back when the owner texts /desktop."))
    out.append(f"Failover: {'on' if cfg['auto'] else 'OFF'}; the other machine takes the phone after "
               f"{int(cfg['stale'] // 60)} min without an answer from {holder or 'the holder'}.")
    beats = heartbeats(now)
    if not beats:
        out.append("No heartbeats yet: open the JARVIS app (or let the routines tick run) on each machine.")
    for h in beats:
        state = "up" if _alive(h, cfg["stale"]) else "QUIET"
        bits = [f"{state}, last heard {_ago(h['age'])}", f"role {h.get('role')}",
                "app open" if h.get("app") else "app shut", "answering" if h.get("answering") else "not answering",
                f"{h.get('workers', 0)} worker(s)"]
        if not h.get("can_answer"):
            bits.append("no Telegram token")
        out.append(f"- {h['machine']}{' (this machine)' if h['machine'].lower() == here().lower() else ''}: "
                   + ", ".join(bits))
    live = [j for j in jobs() if j["status"] in ("queued", "assigned", "running")]
    done = [j for j in jobs() if j["status"] not in ("queued", "assigned", "running")]
    out.append(f"Shared jobs: {len(live)} open, {len(done)} finished.")
    for j in live + done[-5:]:
        where = f" on {j['machine']}" if j.get("machine") else ""
        out.append(f"- {j['id']} [{j['status']}]{where} {j.get('task') or ''} {j.get('worker') or ''}: "
                   f"{' '.join(j['brief'].split())[:80]}")
    return out
