"""The owner's Master Key spy tools, in the DICloak browser (`bin/jarvis dicloak ...`).

DICloak opens the Master Key profile in its own Chromium build, ginsbrowser.exe. The profile is
locked down by the provider (no debugging port), so JARVIS and its agents use it the way a person
does: through computer use (screenshots and clicks). There is one screen and one mouse, so only
one agent may drive it at a time: `lock` before, `unlock` after. A lock older than 30 minutes is
treated as abandoned. The method and which tool to use for which job are in the master-key-tools
skill.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from . import paths

LOCK_MINUTES = 30
SHOW_WINDOW = r"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
                 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
"@
Get-Process ginsbrowser -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object {
  [W]::ShowWindow($_.MainWindowHandle, 3) | Out-Null; [W]::SetForegroundWindow($_.MainWindowHandle) | Out-Null
  $_.MainWindowTitle }
"""


class DICloakError(Exception):
    pass


def _lock_file():
    return paths.home() / "scratch" / "dicloak.lock"


def _powershell(script: str) -> str:
    if os.name != "nt":
        raise DICloakError("DICloak runs on the Windows PC only.")
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=paths.NO_WINDOW)
    return proc.stdout.strip()


def windows() -> list[str]:
    """Titles of the open Master Key profile windows (empty if DICloak or the profile isn't open)."""
    out = _powershell("Get-Process ginsbrowser -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } "
                      "| ForEach-Object { $_.MainWindowTitle }")
    return [line for line in out.splitlines() if line.strip()]


def show() -> list[str]:
    """Maximise the profile window and bring it to the front, ready for screenshots."""
    titles = [t for t in _powershell(SHOW_WINDOW).splitlines() if t.strip()]
    if not titles:
        raise DICloakError("The Master Key profile isn't open. Open DICloak and start the Master Key profile "
                           "(or ask the owner to), then try again.")
    return titles


def holder() -> dict | None:
    try:
        lock = json.loads(_lock_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return lock if time.time() - lock.get("since", 0) < LOCK_MINUTES * 60 else None


def lock(who: str) -> dict:
    current = holder()
    if current and current["who"] != who:
        mins = int((time.time() - current["since"]) / 60)
        raise DICloakError(f"{current['who']} is using the DICloak screen ({mins} min so far). Wait and retry, "
                           "or do other work first.")
    _lock_file().parent.mkdir(parents=True, exist_ok=True)
    data = {"who": who, "since": current["since"] if current else time.time()}
    _lock_file().write_text(json.dumps(data), encoding="utf-8")
    return data


def unlock(who: str) -> bool:
    current = holder()
    if current and current["who"] != who:
        return False
    try:
        _lock_file().unlink()
    except FileNotFoundError:
        pass
    return True
