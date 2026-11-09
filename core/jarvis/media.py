"""Watch reels and videos the owner sends: `bin/jarvis watch <url>`.

Instagram reels, TikToks and YouTube videos are downloaded, every word is transcribed
locally (free, no API) and frames are taken, under brain/files/reels/<id>/. A memory
entry `Reel: ...` holds the caption, the owner's note and the full transcript, so any
agent can search it. Then the video is deleted and only 4 evenly spaced frames are kept,
to look at for anything shown on screen (owner rule M-261109-7767: never keep videos).

The heavy lifting runs in a separate venv (yt-dlp + faster-whisper + instaloader) kept outside
the repo at %LOCALAPPDATA%/JARVIS/media-venv; `bin/jarvis watch --setup` creates it.

Instagram refuses some anonymous downloads (login-only posts, rate limits). The worker tries a
fallback chain (see media_worker.py); a logged-in cookie file at brain/secrets/instagram-cookies.txt
(Netscape cookies.txt format, never a password) is used when there is one. When every route fails,
the link goes into brain/work/reels-pending.txt and the `reels-retry` routine tries it again every
tick, so a late success is still watched, saved and reported to the owner by a worker.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import memory, paths

WORKER = Path(__file__).with_name("media_worker.py")
PACKAGES = ("yt-dlp", "faster-whisper", "instaloader")
DOWNLOAD_FAILED = 3  # the worker's exit code when every download route failed
BACKOFF = "10,30"  # seconds between yt-dlp retries on a direct watch; queue retries don't wait
GIVE_UP_DAYS = 14
KEEP_FRAMES = 4  # owner rule M-261109-7767: keep the key info, never the video


class MediaError(Exception):
    pass


class DownloadFailed(MediaError):
    """Every download route failed; the link was queued for retries."""


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


def transcribe(path: Path) -> str:
    """Words spoken in an audio file (a Telegram voice note), transcribed locally."""
    if not venv_python().exists():
        raise MediaError("The transcriber isn't installed yet. Run: bin/jarvis watch --setup")
    proc = subprocess.run([str(venv_python()), "-I", str(WORKER), "--transcribe", str(path)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", creationflags=paths.NO_WINDOW,
                          env=dict(os.environ, HF_HUB_DISABLE_SYMLINKS_WARNING="1"))
    if proc.returncode != 0:
        raise MediaError("Couldn't transcribe that voice note.")
    return proc.stdout.strip()


def reel_dir(url: str) -> Path:
    m = re.search(r"/(?:reel|reels|p|shorts|video)/([\w-]+)|[?&]v=([\w-]+)|youtu\.be/([\w-]+)", url)
    key = next((g for g in (m.groups() if m else ()) if g), None) or re.sub(r"[^\w]+", "-", url)[-40:]
    return paths.files_dir() / "reels" / key


def cookies_path() -> Path:
    return paths.home() / "secrets" / "instagram-cookies.txt"


def key_frames(folder: Path, count: int = KEEP_FRAMES) -> list[Path]:
    """`count` evenly spaced frames out of a folder of mm-ss.jpg frames (all of them when there are fewer)."""
    frames = sorted(folder.glob("*.jpg"))
    if len(frames) <= count:
        return frames
    return [frames[int((i + 0.5) * len(frames) / count)] for i in range(count)]


def drop_video(folder: Path, keep: list[Path]) -> None:
    """Once the memory entry is saved: delete the downloaded video and every frame except `keep`."""
    for video in folder.glob("video.*"):
        video.unlink(missing_ok=True)
    for frame in (folder / "frames").glob("*.jpg"):
        if frame not in keep:
            frame.unlink(missing_ok=True)


def _worker_cmd() -> list[str]:
    fake = os.environ.get("JARVIS_MEDIA_WORKER")  # tests stand a fake worker in
    if fake:
        return [sys.executable, fake]
    if not venv_python().exists():
        raise MediaError("The video tools aren't installed yet. Run: bin/jarvis watch --setup")
    return [str(venv_python()), "-I", str(WORKER)]


def watch(conn, url: str, *, note: str = "", category: str = "creative-inspiration", product=None,
          queue: bool = True, backoff: str = BACKOFF):
    """Download, transcribe and file one video. Returns (memory entry, folder).

    When no route can download it, the link is queued for the reels-retry routine (unless queue=False) and
    DownloadFailed is raised."""
    cmd = _worker_cmd()
    out = reel_dir(url)
    args = [url, str(out), "--backoff", backoff or ""]
    if cookies_path().exists():
        args += ["--cookies", str(cookies_path())]
    proc = subprocess.run(cmd + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=paths.NO_WINDOW,
                          env=dict(os.environ, HF_HUB_DISABLE_SYMLINKS_WARNING="1"))
    if proc.returncode == DOWNLOAD_FAILED:
        try:
            why = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            why = {"tried": [], "login_only": False}
        reason = ("login-only post" if why.get("login_only") else "download refused") + \
            f" (tried {', '.join(t.split(':')[0] for t in why.get('tried', [])) or 'every route'})"
        if queue:
            add_pending(url, note=note, category=category, product=product, reason=reason)
        raise DownloadFailed(f"Couldn't download {url}: {reason}." + (
            f" Queued in {paths.rel(pending_path())}; the reels-retry routine tries again every 15 minutes."
            if queue else ""))
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise MediaError("Couldn't watch that video: " + " ".join(tail))
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    transcript = (out / "transcript.md").read_text(encoding="utf-8").strip()
    who = info.get("uploader") or "unknown"
    first = (info.get("caption") or info.get("title") or "").strip().splitlines()
    title = f"Reel: {who} - {first[0][:70] if first else info.get('id')}"
    keep = key_frames(out / "frames")
    at = ", ".join(f.stem.replace("-", ":") for f in keep)
    body = "\n\n".join(filter(None, [
        f"**Link:** {info['url']}  \n**By:** {who}" + (f"  \n**Posted:** {info['upload_date']}" if info.get("upload_date") else ""),
        f"**Owner's note:** {note}" if note else "",
        f"**Caption:**\n{info['caption'].strip()}" if info.get("caption") else "",
        f"**Transcript** (local Whisper, may mishear names):\n{transcript or '(no speech)'}",
        f"**Frames:** {paths.rel(out / 'frames')} ({len(keep)} key frames" + (f" at {at}" if at else "") +
        "; the video itself was deleted after watching). Open them for anything shown on screen.",
    ]))
    entry, _ = memory.remember(conn, title=title, body=body + "\n", category=category, provenance="evidence",
                               product=product, tags=["reel", "video", "owner-sent"], confidence="confirmed",
                               source=f"video by {who}", source_ref=info["url"], force=True)
    drop_video(out, keep)
    info.update(frames=len(keep), video="deleted")
    (out / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return entry, out


# The pending queue: links no route could download yet, retried by the reels-retry routine.

def pending_path() -> Path:
    return paths.work_dir() / "reels-pending.txt"


def load_pending() -> list[dict]:
    """One link per line, then a tab and its details as JSON. A bare link added by hand works too."""
    try:
        lines = pending_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items = []
    for line in lines:
        url, _, extra = line.strip().partition("\t")
        if not url or url.startswith("#"):
            continue
        try:
            item = json.loads(extra) if extra else {}
        except ValueError:
            item = {}
        items.append({"note": "", "category": "creative-inspiration", "product": None, "tries": 0,
                      "added": paths.now(), **item, "url": url})
    return items


def save_pending(items: list[dict]) -> None:
    pending_path().parent.mkdir(parents=True, exist_ok=True)
    lines = [i["url"] + "\t" + json.dumps({k: v for k, v in i.items() if k != "url"}) for i in items]
    tmp = pending_path().with_suffix(".tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    tmp.replace(pending_path())


def add_pending(url: str, *, note: str = "", category: str = "creative-inspiration", product=None,
                reason: str = "") -> dict:
    items = load_pending()
    key = reel_dir(url).name
    for item in items:
        if reel_dir(item["url"]).name == key:  # already queued: keep its history, refresh the details
            item.update({k: v for k, v in (("note", note), ("product", product), ("reason", reason)) if v})
            save_pending(items)
            return item
    item = {"url": url, "note": note, "category": category, "product": product, "reason": reason,
            "tries": 0, "added": paths.now()}
    save_pending(items + [item])
    return item


def retry_due(item: dict, now: datetime) -> bool:
    """Every tick for the first two hours, then hourly, then every six hours."""
    if not item.get("last"):
        return True
    tries = item.get("tries", 0)
    gap = 14 if tries < 8 else 59 if tries < 32 else 359
    return now - datetime.fromisoformat(item["last"]) >= timedelta(minutes=gap)


def _report_worker(brief: str) -> dict:
    from . import work
    return work.start(brief, task="", agent="")


def _alert(text: str) -> None:
    from . import telegram
    try:
        telegram.alert(text)
    except Exception:  # the routine carries on without a phone
        pass


def _refresh_downloader(now: datetime) -> None:
    """Instagram changes often and yt-dlp follows within days: upgrade it once a day while links wait."""
    state = paths.work_dir() / "reels-retry.json"
    try:
        last = json.loads(state.read_text(encoding="utf-8")).get("updated")
    except (OSError, ValueError):
        last = None
    if last == now.date().isoformat() or os.environ.get("JARVIS_MEDIA_WORKER") or not venv_python().exists():
        return
    subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "--upgrade", "yt-dlp", "instaloader"],
                   capture_output=True, timeout=600, creationflags=paths.NO_WINDOW)
    state.write_text(json.dumps({"updated": now.date().isoformat()}), encoding="utf-8")


def retry_pending(conn, now: datetime | None = None, start_worker=_report_worker) -> dict:
    """Try each queued link that's due. Watched ones leave the queue and a worker tells the owner what they teach."""
    now = now or datetime.now()
    items = load_pending()
    watched, gave_up, keep = [], [], []
    if any(retry_due(i, now) for i in items):
        _refresh_downloader(now)
    for item in items:
        if not retry_due(item, now):
            keep.append(item)
            continue
        try:
            entry, _ = watch(conn, item["url"], note=item.get("note") or "", category=item.get("category") or
                             "creative-inspiration", product=item.get("product"), queue=False, backoff="")
            watched.append({**item, "entry": entry.id, "title": entry.title})
            continue
        except MediaError as exc:
            item.update(tries=item.get("tries", 0) + 1, last=now.isoformat(timespec="seconds"), error=str(exc)[:300])
        if now - datetime.fromisoformat(item["added"][:19]) > timedelta(days=GIVE_UP_DAYS):
            gave_up.append(item)
        else:
            keep.append(item)
    if items:  # keep links queued while this ran (a watch takes minutes)
        save_pending(keep + [i for i in load_pending() if i["url"] not in {x["url"] for x in items}])
    if gave_up:
        with open(paths.work_dir() / "reels-failed.txt", "a", encoding="utf-8") as f:
            for i in gave_up:
                f.write(f"{paths.now()}\t{i['url']}\t{i.get('error', '')}\n")
        _alert(f"I gave up on {len(gave_up)} reel(s) after {GIVE_UP_DAYS} days: Instagram won't let me download "
               "them. Send the video file itself on Telegram and I'll watch it: "
               + " ".join(i["url"] for i in gave_up))
    worker = None
    if watched:
        lines = "\n".join(f"- {w['url']} -> {w['entry']} ({w['title']})" + (f"; owner's note: {w['note']}" if w.get("note") else "")
                          for w in watched)
        worker = start_worker(
            "Reels the owner sent earlier could not be downloaded at the time; the reels-retry routine has now "
            f"watched them:\n{lines}\n\nFor each one read the memory entry (`bin/jarvis mem get <id>`) and look at a "
            "few frames. Then send the owner ONE short plain Telegram message (`bin/jarvis alert '<text>'`): per reel, "
            "what it teaches and how it applies to Urban Supplied. Check Blake's method first (`bin/jarvis mem search "
            "'<topic>' --provenance mentor`) and say where it agrees or differs; label your own ideas JARVIS "
            "recommendation. Save any lesson worth keeping with `bin/jarvis mem add`. No approval-gated actions.")
    return {"watched": watched, "gave_up": gave_up, "waiting": len(keep), "worker": worker and worker.get("id")}
