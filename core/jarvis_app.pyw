"""JARVIS desktop app: double-click (via the JARVIS shortcut) to open the dashboard in its own window.

Starts the dashboard server in the background (no console window) unless it is already running,
waits until it answers, then opens it as an app window (no address bar or tabs) in Chrome or Edge.
`--server` only starts the server, with no window: the Startup-folder shortcut uses it so JARVIS (and the owner's
Telegram) is back after every restart. `python core/jarvis_app.pyw --install` creates the Desktop and Start-menu
shortcuts (JARVIS, which opens the one app, and Reset JARVIS) and the Startup shortcut (JARVIS background), and
removes the JARVIS Chat shortcut of the separate chat page that no longer exists (T110).
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CORE = Path(__file__).resolve().parent
REPO = CORE.parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
DETACHED = 0x00000008   # DETACHED_PROCESS


def running() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/live", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def pull_brain() -> None:
    """On the standby laptop: fetch the brain the PC last pushed to OneDrive before anything opens it (T105)."""
    sys.path.insert(0, str(CORE))
    from jarvis import paths
    if paths.standby():
        try:
            subprocess.run([sys.executable, str(CORE / "jarvis_cli.py"), "sync", "pull"], cwd=REPO, timeout=300,
                           capture_output=True, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass  # the routines tick tries again in 15 minutes


def start_server() -> None:
    pull_brain()
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw if pyw.exists() else sys.executable)
    log = REPO / "brain" / "scratch" / "dashboard.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        subprocess.Popen([exe, str(CORE / "jarvis_cli.py"), "dashboard", "--no-open"], cwd=REPO,
                         stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW | DETACHED, close_fds=True)


def open_window() -> None:
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if browser:
        # A dedicated profile so the window's flags always apply (they're ignored if Chrome is already running),
        # sound can play when JARVIS wakes without a click first, and mic permission is remembered for JARVIS only.
        profile = Path(os.environ.get("LOCALAPPDATA", str(REPO))) / "JARVIS" / "browser-profile"
        subprocess.Popen([browser, f"--app={URL}", "--window-size=1600,960", f"--user-data-dir={profile}",
                          "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check"],
                         creationflags=DETACHED, close_fds=True)
    else:
        os.startfile(URL)


def launch() -> None:
    if not running():
        start_server()
        for _ in range(60):  # up to ~30 s on a cold start (the voice model loads in the background)
            time.sleep(0.5)
            if running():
                break
    open_window()


def reset() -> None:
    """The Reset JARVIS shortcut, for when he's stuck: stop whatever he's doing (he picks the conversation up
    again with the next message). If the app itself isn't answering, restart it. Says what happened in a box."""
    import ctypes
    import json

    def tell(text: str) -> None:
        ctypes.windll.user32.MessageBoxW(0, text, "JARVIS", 0x40 | 0x10000)  # information icon, on top

    token = (REPO / "brain" / "secrets" / "dashboard_token")
    try:
        req = urllib.request.Request(URL + "api/stop", b"{}", method="POST", headers={
            "Content-Type": "application/json", "X-Jarvis-Token": token.read_text(encoding="utf-8").strip()})
        with urllib.request.urlopen(req, timeout=10) as r:
            if json.loads(r.read() or b"{}").get("ok"):
                return tell("JARVIS has been reset, sir. Whatever he was stuck on is stopped, and any messages "
                            "you sent meanwhile are being answered now.")
    except (OSError, ValueError):
        pass
    # The app isn't answering: end it and start it again.
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Where-Object { $_.CommandLine -match "
          "'jarvis_cli.py.? dashboard' -and $_.CommandLine -notmatch '--port' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW)
    time.sleep(2)
    start_server()
    for _ in range(60):
        time.sleep(0.5)
        if running():
            return tell("JARVIS wasn't answering, sir, so I've restarted him. He's back up now.")
    tell("JARVIS didn't come back up, sir. The details are in brain/scratch/dashboard.log.")


# ---------------------------------------------------------------- icon and shortcuts

def make_icon(path: Path, size: int = 64) -> None:
    """Draw a glowing blue orb (the dashboard's JARVIS core) as a 32-bit .ico, no image libraries needed."""
    px = bytearray()
    c = (size - 1) / 2
    for y in range(size - 1, -1, -1):  # BMP rows are stored bottom-up
        for x in range(size):
            dx, dy = (x - c) / c, (y - c) / c
            r = (dx * dx + dy * dy) ** 0.5
            if r <= 0.78:  # the orb, lit from the top left
                hl = max(0.0, 1 - (((x - c * 0.7) / c) ** 2 + ((y - c * 0.6) / c) ** 2) ** 0.5 * 1.4)
                b, g, rr = 248, int(150 + 80 * hl), int(40 + 200 * hl)
                a = 255
            elif r <= 1.0:  # glow ring
                k = (1.0 - r) / 0.22
                b, g, rr, a = 248, 189, 56, int(200 * k)
            else:
                b = g = rr = a = 0
            px += bytes((b, g, rr, a))
    mask = bytes(((size + 31) // 32) * 4 * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(px) + len(mask), 0, 0, 0, 0)
    image = header + px + mask
    path.write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(image), 22) + image)


def install() -> None:
    icon = CORE / "dashboard" / "jarvis.ico"
    make_icon(icon)
    pyw = Path(sys.executable).with_name("pythonw.exe")
    target = str(pyw if pyw.exists() else sys.executable)
    script = str(Path(__file__).resolve())
    # Ask Windows for the real folders: the Desktop is often redirected into OneDrive.
    shortcuts = (("JARVIS", "", "JARVIS - Urban Supplied AI"),
                 ("Reset JARVIS", " --reset", "JARVIS stuck? Stops what he is doing, or restarts him if frozen"))
    places = [(folder, s) for folder in ("Desktop", "Programs") for s in shortcuts]
    places.append(("Startup", ("JARVIS background", " --server", "Starts JARVIS and the phone bridge at sign-in")))
    for folder, (name, extra, about) in places:
        ps = ("$d = [Environment]::GetFolderPath('{f}'); $p = Join-Path $d '{n}.lnk'; "
              "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); $s.TargetPath = '{t}'; "
              "$s.Arguments = '\"{a}\"{x}'; $s.WorkingDirectory = '{w}'; $s.IconLocation = '{i},0'; "
              "$s.Description = '{d}'; $s.Save(); $p").format(f=folder, n=name, t=target, a=script, x=extra, w=REPO,
                                                              i=icon, d=about)
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        print(f"Shortcut: {r.stdout.strip() or r.stderr.strip()}")
    # The separate chat page is gone: its shortcut would only open the same app, so it is removed.
    gone = ("foreach ($f in 'Desktop', 'Programs') { $p = Join-Path ([Environment]::GetFolderPath($f)) 'JARVIS Chat.lnk'; "
            "if (Test-Path $p) { Remove-Item $p -Confirm:$false; \"Removed: $p\" } }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", gone], capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.stdout.strip():
        print(r.stdout.strip())


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    elif "--reset" in sys.argv:
        reset()
    elif "--server" in sys.argv:
        if not running():
            start_server()
    else:
        launch()   # an old "JARVIS Chat" shortcut (--chat) opens the same one app
