"""Brain sync between the primary PC and the standby laptop (T105, D69).

Only the text brain travels: a jarvis.db snapshot, memory, journal, work notes, reports, config and the private
skills, plus a separate secrets zip. Everything big that can be downloaded again (voice venvs and models, tools,
reel videos and frames, images, backups) stays on each machine. Two fixed-name zips in the owner's private
OneDrive folder are overwritten in place, so the cloud copy never grows:

    OneDrive/JARVIS-laptop-transfer/jarvis-brain-lean.zip     the brain (text only)
    OneDrive/JARVIS-laptop-transfer/jarvis-secrets-lean.zip   brain/secrets
    OneDrive/JARVIS-laptop-transfer/sync.json                 when, from where, how big

The primary pushes (routine `brain-sync`, every 2 hours, skipped when nothing changed). A standby pulls on start
and on every routines tick when the PC has pushed something newer. The database is restored through SQLite's
backup API, so a pull is safe while the dashboard has it open. Machine-local settings (`LOCAL_CONFIG_KEYS`) and
the last-pull record (kept outside brain/, next to the role file) are never overwritten.

    bin/jarvis sync              status
    bin/jarvis sync push [--force]
    bin/jarvis sync pull [--force]

Since T119 the standby's own work comes back too: before each pull it writes an outbox of what it made or changed,
which the primary merges on every tick, and after the pull it puts back whatever the primary hasn't merged yet.
See twoway.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths, records

BRAIN_ZIP = "jarvis-brain-lean.zip"
SECRETS_ZIP = "jarvis-secrets-lean.zip"
MANIFEST = "sync.json"

# Folders that never travel: machine-local or re-downloadable. Secrets go in their own zip.
SKIP_DIRS = {"backups", "scratch", "logs", "tools", "secrets", "tts", "call"}
SKIP_DIR_NAMES = {"frames", "__pycache__", "node_modules", ".git"}
SKIP_FILES = {"jarvis.db", "jarvis.db-wal", "jarvis.db-shm", "STANDBY-LAPTOP.txt", "grant.json"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".html", ".htm", ".srt", ".vtt",
                 ".py", ".js", ".css", ".ps1", ".cmd", ".sh", ".liquid", ".xml", ".svg"}
WHOLE_DIRS = {"skills"}  # private Blake playbooks: small, and their example images belong with them
MAX_TEXT_BYTES = 5_000_000
LOCAL_CONFIG_KEYS = ("sip_bind",)  # each machine binds the call server to its own Tailscale address


class SyncError(records.RecordError):
    pass


def sync_dir() -> Path:
    env = os.environ.get("JARVIS_SYNC_DIR")
    if env:
        return Path(env)
    cfg = paths.config().get("sync_dir")
    return Path(cfg) if cfg else Path.home() / "OneDrive" / "JARVIS-laptop-transfer"


def _local_state_path() -> Path:
    return paths.role_file().with_name("sync.json")


def local_state() -> dict:
    try:
        return json.loads(_local_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_local_state(st: dict) -> None:
    p = _local_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=1), encoding="utf-8")


def manifest() -> dict:
    try:
        return json.loads((sync_dir() / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def files() -> list[tuple[Path, str]]:
    """Every brain file that travels, as (path, relative name)."""
    home = paths.home()
    found = []
    for root, dirs, names in os.walk(home, followlinks=True):
        rel_root = Path(root).relative_to(home).as_posix()
        rel_root = "" if rel_root == "." else rel_root + "/"
        top = (rel_root.split("/", 1)[0]) if rel_root else ""
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIR_NAMES and (rel_root or d not in SKIP_DIRS))
        for n in sorted(names):
            f = Path(root) / n
            if n in SKIP_FILES or n.endswith(".tmp"):
                continue
            if top not in WHOLE_DIRS:
                try:
                    if f.suffix.lower() not in TEXT_SUFFIXES or f.stat().st_size > MAX_TEXT_BYTES:
                        continue
                except OSError:
                    continue
            found.append((f, rel_root + n))
    return found


def fingerprint(found: list[tuple[Path, str]]) -> str:
    h = hashlib.sha1()
    for f, rel in found + [(paths.db_path(), "jarvis.db"), (paths.home() / "jarvis.db-wal", "jarvis.db-wal")]:
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def _write_atomic(dest: Path, build) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        build(zf)
    try:
        os.replace(tmp, dest)
    except OSError as exc:  # OneDrive can hold the old copy for a moment while it uploads
        tmp.unlink(missing_ok=True)
        raise SyncError(f"couldn't replace {dest.name} ({exc}); the next run tries again") from exc


def push(conn, force: bool = False) -> dict:
    """Write the lean brain and secrets to the sync folder. Primary only, skipped when nothing changed."""
    if paths.standby() and not force:
        raise SyncError("This machine is the standby: it pulls, the primary pushes (use --force to override).")
    out = sync_dir()
    out.mkdir(parents=True, exist_ok=True)
    found = files()
    fp = fingerprint(found)
    old = manifest()
    if not force and old.get("fingerprint") == fp and (out / BRAIN_ZIP).exists():
        return {"pushed": False, "note": "nothing changed since the last push", **old}

    def brain(zf):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "jarvis.db"
            target = sqlite3.connect(snap)
            conn.backup(target)
            target.close()
            zf.write(snap, "jarvis.db")
        for f, rel in found:
            try:
                zf.write(f, rel)
            except OSError:
                pass  # a file removed mid-walk

    _write_atomic(out / BRAIN_ZIP, brain)
    secrets = paths.home() / "secrets"
    if secrets.is_dir():
        _write_atomic(out / SECRETS_ZIP, lambda zf: [zf.write(f, f.relative_to(paths.home()).as_posix())
                                                     for f in sorted(secrets.rglob("*")) if f.is_file()])
    from . import phone  # the same machine name the phone switch uses
    m = {"stamp": datetime.now().astimezone().isoformat(timespec="seconds"), "machine": phone.this_machine(),
         "role": paths.machine_role(), "files": len(found) + 1, "bytes": (out / BRAIN_ZIP).stat().st_size,
         "fingerprint": fp}
    (out / MANIFEST).write_text(json.dumps(m, indent=1), encoding="utf-8")
    return {"pushed": True, "note": f"pushed {m['files']} files, {m['bytes'] / 1e6:.1f} MB", **m}


def _rebase_memory_paths(conn) -> None:
    """The index keys memory files by path; point the pushed paths at this machine's brain, or the next memory sync
    would take every file for a copy and give it a new id."""
    row = conn.execute("SELECT path FROM memory LIMIT 1").fetchone()
    if not row:
        return
    p = row[0].replace("\\", "/")
    at = 0 if p.startswith("memory/") else p.find("/memory/") + 1
    if at == 0 and not p.startswith("memory/"):
        return
    old, new = p[:at] + "memory/", paths.rel(paths.memory_dir()).replace("\\", "/") + "/"
    if old != new:
        conn.execute("UPDATE memory SET path = ? || substr(path, ?) WHERE path LIKE ? || '%'",
                     (new, len(old) + 1, old))
        conn.commit()


def pull(conn, force: bool = False) -> dict:
    """Restore the newest pushed brain onto this (standby) machine, when it's newer than the last pull."""
    if not paths.standby() and not force:
        raise SyncError("This machine is the primary: it pushes. Pulling would overwrite the live brain "
                        "(use --force only when handing back from the laptop).")
    m = manifest()
    src = sync_dir() / BRAIN_ZIP
    if not m or not src.exists():
        return {"pulled": False, "note": f"nothing pushed yet in {sync_dir()}"}
    st = local_state()
    if not force and not _newer_push(m, st):
        return {"pulled": False, "note": f"already up to date ({m['stamp']})", **m}
    from . import twoway
    # T119: this machine's own work goes into its outbox first, so the pull can't lose it.
    twoway.export(conn)
    here = twoway.machine().lower()
    sender = m.get("machine") or "primary"
    home = paths.home()
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config.json"
    if os.name == "nt":  # mentor captures nest deep: get past the 260-character path limit
        home = Path("\\\\?\\" + str(home.resolve()))
    try:
        local_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        local_cfg = {}
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(src) as zf:
        for name in zf.namelist():
            if name == "jarvis.db":
                zf.extract(name, tmp)
                source = sqlite3.connect(Path(tmp) / "jarvis.db")
                source.backup(conn)  # in place: other open connections stay valid
                source.close()
                _rebase_memory_paths(conn)
            elif ".." in Path(name).parts or Path(name).is_absolute():
                continue
            elif name.startswith("work/remote/"):
                # The sender's mirror of this machine's own runs: the real ones are here already.
                if name.split("/")[2].lower() != twoway._safe(here).lower():
                    zf.extract(name, home)
            elif name.startswith("work/runs/"):
                _mirror_run(zf, name, sender)
            else:
                zf.extract(name, home)
        sec = sync_dir() / SECRETS_ZIP
        if sec.exists():
            with zipfile.ZipFile(sec) as sz:
                sz.extractall(home)
        twoway.save_baseline(Path(tmp) / "jarvis.db", zf, m.get("stamp"), time.time())
    mine = twoway.reapply(conn)  # this machine's work the primary hasn't merged yet goes back on top
    keep = {k: local_cfg[k] for k in LOCAL_CONFIG_KEYS if k in local_cfg}
    if keep:
        try:
            pulled = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pulled = {}
        cfg_path.write_text(json.dumps({**pulled, **keep}, indent=2), encoding="utf-8")
    _save_local_state({"pulled_stamp": m["stamp"], "pulled_fp": m.get("fingerprint"), "pulled_at": paths.now(),
                       "from": m.get("machine")})
    note = f"pulled the brain pushed {m['stamp']} by {m.get('machine')}"
    if mine and (mine["added"] or mine["updated"] or mine["files"]):
        note += (f"; kept {mine['added'] + mine['updated']} record(s) and {mine['files']} file(s) of this machine's "
                 "own work the primary hasn't merged yet")
    return {"pulled": True, "note": note, **m}


def _newer_push(m: dict, st: dict) -> bool:
    """A push the standby hasn't pulled. Two pushes can share a stamp (a merge pushes straight away), so the
    fingerprint counts too."""
    return st.get("pulled_stamp") != m.get("stamp") or st.get("pulled_fp", m.get("fingerprint")) != m.get("fingerprint")


def _mirror_run(zf, name: str, sender: str) -> None:
    """A worker run from the other machine is kept as a read-only mirror, never in this machine's runs/, so it is
    never resumed here. A copy an older pull left in runs/ (same session) moves out; a different run that happens
    to share the number is this machine's own and stays."""
    from . import twoway
    dest = twoway.remote_runs_dir(sender) / Path(name).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = zf.read(name)
    dest.write_bytes(data)
    local = paths.home() / name
    if local.suffix == ".json" and local.exists():
        try:
            same = json.loads(local.read_text(encoding="utf-8")).get("session") == json.loads(data).get("session")
        except (OSError, ValueError):
            same = False
        if same:
            local.unlink(missing_ok=True)


def status() -> dict:
    m, st = manifest(), local_state()
    return {"role": paths.machine_role(), "sync_dir": str(sync_dir()), "last_push": m, "last_pull": st,
            "behind": bool(m) and paths.standby() and _newer_push(m, st)}


def status_line() -> str:
    s = status()
    m = s["last_push"]
    if not m:
        return f"Brain sync: nothing pushed yet ({s['sync_dir']})"
    line = f"Brain sync: last push {m['stamp'][:16]} from {m.get('machine')}, {m['bytes'] / 1e6:.1f} MB"
    if s["role"] == "standby":
        line += "; this standby is " + ("BEHIND" if s["behind"] else "up to date")
    try:  # T119: the way back
        from . import twoway
        line += "; " + twoway.status_bit()
    except Exception:
        pass
    return line
