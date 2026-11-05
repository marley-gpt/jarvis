"""Live updates for the JARVIS app: change the code and the open window catches up on its own.

Every page gets /live.js, which polls /api/live. When a file in core/dashboard/ changes, the
version there changes and each window reloads itself (once the owner isn't typing and JARVIS isn't
talking). When JARVIS's own Python changes, the server waits until JARVIS is between replies, checks
the new code imports cleanly, then starts a fresh copy of itself and exits; the window sees a new
boot id and reloads onto it. Broken code is never swapped in: the old server keeps running.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
import zlib
from pathlib import Path

from . import paths

UI_DIR = paths.CORE_DIR / "dashboard"
CODE_DIR = paths.CORE_DIR / "jarvis"
BOOT = uuid.uuid4().hex[:12]  # changes each time the server starts
SCRIPT = '<script src="/live.js"></script>'
SETTLE = 3.0  # seconds a change must sit still, so a half-saved file or a branch switch isn't picked up midway
NO_WINDOW, DETACHED = 0x08000000, 0x00000008


def fingerprint(folder: Path, pattern: str) -> str:
    """Changes whenever a matching file in the folder is added, removed or saved."""
    stamps = []
    for f in sorted(folder.rglob(pattern)):
        try:
            stamps.append(f"{f.relative_to(folder)}:{f.stat().st_mtime_ns}")
        except OSError:  # deleted while we looked
            continue
    return f"{zlib.crc32(chr(10).join(stamps).encode()):08x}"


def ui_version() -> str:
    return fingerprint(UI_DIR, "*.*")


def state() -> dict:
    return {"ui": ui_version(), "boot": BOOT}


def inject(html: str) -> str:
    """Add the live-update script to a page."""
    if SCRIPT in html:
        return html
    at = html.lower().rfind("</body>")
    return html[:at] + SCRIPT + "\n" + html[at:] if at >= 0 else html + SCRIPT


def code_ok() -> str:
    """Empty if the new code imports; otherwise the error, so a broken edit never replaces the running server."""
    probe = "import jarvis.dashboard, jarvis.cli"
    try:
        done = subprocess.run([sys.executable, "-c", probe], cwd=paths.CORE_DIR, capture_output=True, text=True,
                              timeout=60, creationflags=NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return "" if done.returncode == 0 else (done.stderr.strip().splitlines() or ["import failed"])[-1]


class Watcher:
    """Restarts the server onto new Python code when JARVIS is idle. `restart` does the swap (see serve())."""

    def __init__(self, busy, restart, publish, interval: float = 1.0):
        self.busy, self.restart, self.publish, self.interval = busy, restart, publish, interval
        self.running = fingerprint(CODE_DIR, "*.py")
        self.failed = ""  # the fingerprint of code that wouldn't import, so it's reported once
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="jarvis-live", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        seen, since = self.running, time.time()
        while not self._stop.wait(self.interval):
            now = fingerprint(CODE_DIR, "*.py")
            if now != seen:
                seen, since = now, time.time()
                continue
            if now in (self.running, self.failed) or time.time() - since < SETTLE or self.busy():
                continue
            error = code_ok()
            if error:
                self.failed = now
                sys.stderr.write(f"jarvis: new code not loaded, it doesn't import: {error}\n")
                self.publish({"type": "error", "text": f"I've kept the old version running, sir. The update has a fault: {error}"})
                continue
            sys.stderr.write("jarvis: code changed, restarting onto it\n")
            self.restart()
            return


def respawn() -> None:
    """Start a fresh copy of this server process, detached, logging where the app's server logs."""
    log = paths.scratch_dir() / "dashboard.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "JARVIS_RESTARTED": "1"}
    flags = NO_WINDOW | DETACHED if os.name == "nt" else 0
    with open(log, "ab") as out:
        subprocess.Popen([sys.executable, *sys.argv], cwd=os.getcwd(), env=env, stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
