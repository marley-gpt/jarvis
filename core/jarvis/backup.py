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


# What a machine move leaves behind: venvs, voice models, tools and reel videos are re-downloaded on the
# new machine (scripts/laptop-setup.ps1), and backups/scratch/logs are local. Secrets go in their own zip.
BUNDLE_SKIP_DIRS = {"backups", "scratch", "logs", "tools", "secrets", "tts/venv", "tts/clone-venv",
                    "tts/design-venv", "tts/models", "tts/piper", "tts/out"}
BUNDLE_SKIP_SUFFIXES = {".mp4", ".part", ".webm", ".mkv", ".mov"}
BUNDLE_SKIP_FILES = {"jarvis.db", "jarvis.db-wal", "jarvis.db-shm", "STANDBY-LAPTOP.txt"}


def bundle(conn, dest_dir: Path, with_secrets: bool = False) -> list[Path]:
    """The whole brain for another machine (T105): a consistent jarvis.db snapshot plus every file except
    what BUNDLE_SKIP_* lists. Returns the zip, and a separate secrets zip when asked."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    home = paths.home()
    dest = dest_dir / f"jarvis-brain-{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "jarvis.db"
        target = sqlite3.connect(snap)
        conn.backup(target)
        target.close()
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, "jarvis.db")
            for f in sorted(home.rglob("*")):
                rel = f.relative_to(home).as_posix()
                if not f.is_file() or f.name in BUNDLE_SKIP_FILES or f.suffix.lower() in BUNDLE_SKIP_SUFFIXES:
                    continue
                if any(rel == d or rel.startswith(d + "/") for d in BUNDLE_SKIP_DIRS):
                    continue
                zf.write(f, rel)
    made = [dest]
    secrets = home / "secrets"
    if with_secrets and secrets.is_dir():
        sdest = dest_dir / f"jarvis-secrets-{stamp}.zip"
        with zipfile.ZipFile(sdest, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(secrets.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(home).as_posix())
        made.append(sdest)
    return made


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
