"""Watch reels and videos the owner sends: `bin/jarvis watch <url>`.

Instagram reels, TikToks and YouTube videos are downloaded, every word is transcribed
locally (free, no API) and frames are saved, under brain/files/reels/<id>/. A memory
entry `Reel: ...` holds the caption, the owner's note and the full transcript, so any
agent can search it; the frames are there to look at for anything shown on screen.

The heavy lifting runs in a separate venv (yt-dlp + faster-whisper) kept outside the
repo at %LOCALAPPDATA%/JARVIS/media-venv; `bin/jarvis watch --setup` creates it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import memory, paths

WORKER = Path(__file__).with_name("media_worker.py")
PACKAGES = ("yt-dlp", "faster-whisper")


class MediaError(Exception):
    pass


def venv() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "JARVIS" / "media-venv"


def venv_python() -> Path:
    return venv() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def setup() -> str:
    """Create the media venv and install the downloader and the transcriber (both free)."""
    if not venv_python().exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv())], check=True)
    subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "--upgrade", *PACKAGES], check=True)
    return str(venv())


def reel_dir(url: str) -> Path:
    m = re.search(r"/(?:reel|reels|p|shorts|video)/([\w-]+)|[?&]v=([\w-]+)|youtu\.be/([\w-]+)", url)
    key = next((g for g in (m.groups() if m else ()) if g), None) or re.sub(r"[^\w]+", "-", url)[-40:]
    return paths.files_dir() / "reels" / key


def watch(conn, url: str, *, note: str = "", category: str = "creative-inspiration", product=None):
    """Download, transcribe and file one video. Returns (memory entry, folder)."""
    if not venv_python().exists():
        raise MediaError("The video tools aren't installed yet. Run: bin/jarvis watch --setup")
    out = reel_dir(url)
    proc = subprocess.run([str(venv_python()), "-I", str(WORKER), url, str(out)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ, HF_HUB_DISABLE_SYMLINKS_WARNING="1"))
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise MediaError("Couldn't watch that video: " + " ".join(tail))
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    transcript = (out / "transcript.md").read_text(encoding="utf-8").strip()
    who = info.get("uploader") or "unknown"
    first = (info.get("caption") or info.get("title") or "").strip().splitlines()
    title = f"Reel: {who} - {first[0][:70] if first else info.get('id')}"
    body = "\n\n".join(filter(None, [
        f"**Link:** {info['url']}  \n**By:** {who}" + (f"  \n**Posted:** {info['upload_date']}" if info.get("upload_date") else ""),
        f"**Owner's note:** {note}" if note else "",
        f"**Caption:**\n{info['caption'].strip()}" if info.get("caption") else "",
        f"**Transcript** (local Whisper, may mishear names):\n{transcript or '(no speech)'}",
        f"**Frames:** {paths.rel(out / 'frames')} ({info['frames']} images, one every {info['frame_every']} s). "
        "Open them for anything shown on screen.",
    ]))
    entry, _ = memory.remember(conn, title=title, body=body + "\n", category=category, provenance="evidence",
                               product=product, tags=["reel", "video", "owner-sent"], confidence="confirmed",
                               source=f"video by {who}", source_ref=info["url"], force=True)
    return entry, out
