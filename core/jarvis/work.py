"""Durable workers: delegated jobs that survive the session that started them.

Agents started with the Agent tool live inside the conversation's process, so when the phone session
restarts they die mid-task and leave nothing behind. A worker is its own detached `claude -p` process
instead, with its brief, session id and log kept in brain/work/runs/, so a restart loses nothing:

    bin/jarvis work start --task T<n> --agent <name> --brief-file <path>   start one
    bin/jarvis work list                                                   what's running, done or stopped
    bin/jarvis work resume [W<n>]                                          restart stopped ones where they left off

The SessionStart hook resumes stopped workers automatically and lists them in the brief.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from . import paths

BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
DETACHED = 0x00000008 | 0x00000200 | BREAKAWAY  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
MAX_ATTEMPTS = 3
RESUME_PROMPT = ("The process running you stopped before you finished. Carry on from where you left off: "
                 "check what you already saved, finish the brief, write the required deliverable and close the task.")


class WorkError(Exception):
    pass


def runs_dir() -> Path:
    d = paths.home() / "work" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(wid: int) -> Path:
    return runs_dir() / f"W{wid}.json"


def _save(run: dict) -> None:
    _path(run["id"]).write_text(json.dumps(run, indent=1), encoding="utf-8")


def _id(wid) -> int:
    try:
        return int(str(wid).upper().lstrip("W"))
    except ValueError:
        raise WorkError(f"Not a worker id: {wid}")


def get(wid) -> dict:
    p = _path(_id(wid))
    if not p.exists():
        raise WorkError(f"No worker W{_id(wid)}.")
    return json.loads(p.read_text(encoding="utf-8"))


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        # Asked of Windows directly: starting tasklist for each running worker cost 0.2 s a worker, on every listing.
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _result(run: dict) -> dict | None:
    """The run's result event: the last line of the log that is one (`--output-format json` writes a single
    object at the end, stream-json writes it as its last event). A killed run has none."""
    log = Path(run["log"])
    if not log.exists():
        return None
    for line in reversed(log.read_text(encoding="utf-8", errors="replace")[-400000:].splitlines()):
        if '"result"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            return ev
    return None


def _finished(run: dict) -> bool:
    ev = _result(run)
    return bool(ev) and not ev.get("is_error")


def refresh(run: dict) -> dict:
    if run["status"] == "running" and not _alive(run.get("pid")):
        run["status"] = "done" if _finished(run) else "stopped"
        _save(run)
    return run


def _spawn(run: dict, prompt: str, resume: bool) -> None:
    from .dashboard import find_claude
    claude = find_claude()
    if not claude:
        raise WorkError("Can't find the claude command.")
    # stream-json, so the log grows as the worker goes and the dashboard can show it live (`tail`).
    cmd = [*claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--model", run.get("model") or paths.config().get("worker_model") or "opus"]
    cmd += ["--resume", run["session"]] if resume else ["--session-id", run["session"]]
    if run.get("agent") and not resume:
        cmd += ["--agent", run["agent"]]
    flags = [paths.NO_WINDOW | DETACHED, paths.NO_WINDOW | (DETACHED & ~BREAKAWAY)] if os.name == "nt" else [0]
    with open(run["log"], "ab") as log:
        for i, f in enumerate(flags):  # breaking away from the parent's job is refused when the job forbids it
            try:
                proc = subprocess.Popen(cmd, cwd=paths.REPO_ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                        creationflags=f, start_new_session=os.name != "nt", close_fds=True)
                break
            except PermissionError:
                if i == len(flags) - 1:
                    raise
    run.update(pid=proc.pid, status="running", attempts=run.get("attempts", 0) + 1, updated=paths.now())
    _save(run)


def start(brief: str, task: str = "", agent: str = "", model: str = "") -> dict:
    brief = (brief or "").strip()
    if not brief:
        raise WorkError("A worker needs a brief.")
    wid = max([_id(p.stem) for p in runs_dir().glob("W*.json")] or [0]) + 1
    run = {"id": wid, "task": task, "agent": agent, "brief": brief, "session": str(uuid.uuid4()),
           "log": str(runs_dir() / f"W{wid}.log"), "status": "new", "started": paths.now(), "attempts": 0, "model": model}
    _spawn(run, brief, resume=False)
    return run


def listing(limit: int = 20) -> list[dict]:
    runs = sorted((json.loads(p.read_text(encoding="utf-8")) for p in runs_dir().glob("W*.json")),
                  key=lambda r: r["id"], reverse=True)
    return [refresh(r) for r in runs[:limit]]


def stop(wid) -> dict:
    """End a running worker on purpose. It's marked cancelled, so it isn't resumed at the next session start."""
    run = refresh(get(wid))
    if run["status"] != "running":
        raise WorkError(f"W{run['id']} isn't running ({run['status']}).")
    pid = run.get("pid")
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=paths.NO_WINDOW)
    else:
        try:
            os.killpg(pid, 15)
        except OSError:
            pass
    run.update(status="cancelled", updated=paths.now())
    _save(run)
    return run


def _line(ev: dict) -> list[str]:
    """What one stream-json event says, in plain lines: the worker's words, the tools it ran, the end."""
    kind = ev.get("type")
    if kind == "result":
        return [("Finished with an error: " if ev.get("is_error") else "Finished: ") + str(ev.get("result") or "")[:600]]
    content = (ev.get("message") or {}).get("content") if isinstance(ev.get("message"), dict) else None
    if kind != "assistant" or not isinstance(content, list):
        return []
    out = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and b.get("text", "").strip():
            out.append(b["text"].strip()[:600])
        elif b.get("type") == "tool_use":
            data = b.get("input") or {}
            what = next((data[k] for k in ("description", "command", "file_path", "pattern", "query", "url", "prompt")
                         if isinstance(data.get(k), str)), "")
            out.append(f"> {b.get('name')}: {' '.join(what.split())[:160]}".rstrip(": "))
    return out


def tail(wid, lines: int = 80) -> dict:
    """The worker's log as readable lines, newest last."""
    run = refresh(get(wid))
    log = Path(run["log"])
    out: list[str] = []
    if log.exists():
        for raw in log.read_text(encoding="utf-8", errors="replace")[-600000:].splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                out.append(raw[:300])  # stderr, or an older plain log
                continue
            if isinstance(ev, dict):
                out += _line(ev)
    return {"id": run["id"], "task": run.get("task"), "agent": run.get("agent"), "status": run["status"],
            "started": run.get("started"), "brief": run.get("brief", "")[:400], "lines": out[-lines:]}


def resume(wid=None) -> list[dict]:
    """Restart stopped workers on their own session. Gives up after MAX_ATTEMPTS so a broken one can't loop."""
    targets = [refresh(get(wid))] if wid else listing(200)
    resumed = []
    for run in targets:
        if run["status"] != "stopped":
            continue
        if run.get("attempts", 0) >= MAX_ATTEMPTS:
            run["status"] = "failed"
            _save(run)
            continue
        _spawn(run, RESUME_PROMPT, resume=True)
        resumed.append(run)
    return resumed
