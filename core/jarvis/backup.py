"""Local backups: a consistent SQLite snapshot plus every memory file, in one zip."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths


def create(conn: sqlite3.Connection, label: str = "manual") -> Path:
    dest_dir = paths.backups_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"jarvis-{datetime.now():%Y%m%d-%H%M%S}-{label}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "jarvis.db"
        target = sqlite3.connect(snap)
        conn.backup(target)
        target.close()
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, "jarvis.db")
            for folder in (paths.memory_dir(), paths.work_dir(), paths.journal_dir()):
                if folder.exists():
                    for f in folder.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(paths.home()).as_posix())
            cfg = paths.home() / "config.json"
            if cfg.exists():
                zf.write(cfg, "config.json")
    prune()
    return dest


def prune() -> list[Path]:
    keep = int(paths.config()["backup_keep"])
    found = sorted(paths.backups_dir().glob("jarvis-*.zip"))
    removed = found[:-keep] if len(found) > keep else []
    for f in removed:
        f.unlink()
    return removed


def latest() -> Path | None:
    found = sorted(paths.backups_dir().glob("jarvis-*.zip")) if paths.backups_dir().exists() else []
    return found[-1] if found else None


def daily(conn) -> Path | None:
    """Back up once per day; called from the SessionStart hook."""
    last = latest()
    if last and last.name[7:15] == f"{datetime.now():%Y%m%d}":
        return None
    if not paths.db_path().exists():
        return None
    return create(conn, "daily")
