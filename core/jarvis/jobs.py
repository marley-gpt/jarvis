"""Desktop jobs: work that needs the screen, ordered from anywhere.

Computer use (the Master Key tools in DICloak, for example) only works in a Claude desktop session
that the owner granted access to at the PC. Scheduled runs and the headless phone session can't ask
for it. So one long-running desktop session, the job runner, holds the grant and everything else
sends it jobs: the phone, the 11am product hunt, any other session.

    bin/jarvis job add '<what to do>'     queue a job (from anywhere)
    bin/jarvis job list                   what's queued, running and recently finished
    bin/jarvis job wait                   the runner blocks here, using no tokens, until a job arrives
    bin/jarvis job done J<n> --summary .. the runner reports back; the owner's phone gets the result

`job wait` also writes a heartbeat, so the phone can tell when the runner isn't running.
"""

from __future__ import annotations

import json
import time

from . import paths

HEARTBEAT_SECONDS = 20
ALIVE_SECONDS = 90  # no heartbeat for this long: the runner isn't running


class JobError(Exception):
    pass


def add(conn, text: str, source: str = "owner") -> int:
    text = " ".join((text or "").split())
    if not text:
        raise JobError("A job needs a description.")
    cur = conn.execute("INSERT INTO jobs (ts, text, source) VALUES (?,?,?)", (paths.now(), text, source))
    conn.commit()
    return cur.lastrowid


def _id(job_id) -> int:
    try:
        return int(str(job_id).upper().lstrip("J"))
    except ValueError as exc:
        raise JobError(f"Not a job id: {job_id}") from exc


def get(conn, job_id):
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (_id(job_id),)).fetchone()
    if row is None:
        raise JobError(f"No job {job_id}")
    return row


def claim(conn):
    """Take the oldest queued job, or None. Only the runner calls this."""
    row = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    conn.execute("UPDATE jobs SET status = 'running', started = ? WHERE id = ?", (paths.now(), row["id"]))
    conn.commit()
    return get(conn, row["id"])


def finish(conn, job_id, summary: str, ok: bool = True):
    row = get(conn, job_id)
    if row["status"] not in ("running", "queued"):
        raise JobError(f"J{row['id']} is already {row['status']}")
    conn.execute("UPDATE jobs SET status = ?, finished = ?, summary = ? WHERE id = ?",
                 ("done" if ok else "failed", paths.now(), summary, row["id"]))
    conn.commit()
    return get(conn, row["id"])


def listing(conn, limit: int = 15):
    return conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def waiting(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0]


def running(conn):
    return conn.execute("SELECT * FROM jobs WHERE status = 'running' ORDER BY id LIMIT 1").fetchone()


def _heartbeat_file():
    return paths.home() / "scratch" / "job-runner.json"


def beat() -> None:
    _heartbeat_file().parent.mkdir(parents=True, exist_ok=True)
    _heartbeat_file().write_text(json.dumps({"at": time.time()}), encoding="utf-8")


def runner_alive() -> bool:
    try:
        return time.time() - json.loads(_heartbeat_file().read_text(encoding="utf-8"))["at"] < ALIVE_SECONDS
    except (OSError, ValueError, KeyError):
        return False


def wait(conn, timeout: float, poll: float = 5.0):
    """Block until a job is queued and claim it, beating the heartbeat. None after `timeout` seconds."""
    deadline, last = time.time() + timeout, 0.0
    while time.time() < deadline:
        if time.time() - last >= HEARTBEAT_SECONDS:
            beat()
            last = time.time()
        job = claim(conn)
        if job:
            return job
        time.sleep(poll)
    return None
