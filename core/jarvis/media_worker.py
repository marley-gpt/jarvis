"""Watch one video: download it, transcribe every word, save the frames. Runs inside the media venv.

    <media-venv python> media_worker.py <url> <out-dir> [--cookies <cookies.txt>] [--backoff 10,30]

Writes video.mp4, info.json (title, uploader, caption, duration, link), transcript.md
([mm:ss] lines, local Whisper) and frames/mm-ss.jpg (every 2 s for short videos,
every 10 s for long ones), then prints info.json's contents. media.watch saves the memory entry, then
deletes video.mp4 and all but 4 frames (owner rule: never keep videos). Needs yt-dlp,
faster-whisper and ffmpeg (`bin/jarvis watch --setup` installs them, plus instaloader).

Instagram often refuses anonymous downloads (a login wall on some posts, a rate limit after many), so the
download walks a fallback chain, all free: a video already dropped in the folder, yt-dlp (retried with
backoff), yt-dlp with the cookies.txt given, yt-dlp with the cookies of a browser that isn't running,
instaloader, then the post's embed page. When every route fails it prints {"error": ..., "tried": [...]}
and exits 3, so the caller can queue the link and try again later.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import yt_dlp
from faster_whisper import WhisperModel

DOWNLOAD_FAILED = 3
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TRANSIENT = re.compile(r"empty media response|rate.?limit|429|timed? ?out|temporar|connection", re.I)


def mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def shortcode(url: str) -> str | None:
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(?:reel|reels|p|tv)/([\w-]+)", url)
    return m.group(1) if m else None


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _ytdlp(url: str, out: Path, **extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "outtmpl": str(out / "video.%(ext)s"), "extractor_retries": 2,
            "format": "b[height<=720]/bv*[height<=720]+ba/b", "merge_output_format": "mp4", **extra}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


def _anonymous(url: str, out: Path, backoff: list[int]) -> dict:
    """yt-dlp without cookies, waiting and retrying when Instagram looks rate-limited."""
    waits = [0, *backoff]
    for i, wait in enumerate(waits):
        time.sleep(wait)
        try:
            return _ytdlp(url, out)
        except Exception as exc:  # yt-dlp raises DownloadError for everything
            if i == len(waits) - 1 or not TRANSIENT.search(str(exc)):
                raise


def _instaloader(url: str, out: Path) -> dict:
    import instaloader
    code = shortcode(url)
    if not code:
        raise RuntimeError("not an Instagram post")
    post = instaloader.Post.from_shortcode(instaloader.Instaloader(quiet=True).context, code)
    if not post.is_video:
        raise RuntimeError("that post isn't a video")
    (out / "video.mp4").write_bytes(_get(post.video_url, 300))
    return {"webpage_url": f"https://www.instagram.com/reel/{code}/", "id": code, "uploader": post.owner_username,
            "description": post.caption or "", "duration": post.video_duration,
            "upload_date": post.date_utc.strftime("%Y%m%d")}


def _embed(url: str, out: Path) -> dict:
    code = shortcode(url)
    if not code:
        raise RuntimeError("not an Instagram post")
    html = _get(f"https://www.instagram.com/p/{code}/embed/captioned/").decode("utf-8", "replace")
    m = re.search(r'video_url\\*"\s*:\s*\\*"(https:.+?)\\*"', html)
    if not m:
        raise RuntimeError("the embed page has no video (login wall)")
    video = m.group(1).replace("\\", "").replace("u0026", "&").replace("&amp;", "&")
    (out / "video.mp4").write_bytes(_get(video, 300))
    who = re.search(r'class="UsernameText"[^>]*>([^<]+)<', html)
    return {"webpage_url": f"https://www.instagram.com/reel/{code}/", "id": code,
            "uploader": who.group(1) if who else None}


def fetch(url: str, out: Path, cookies: str | None = None, backoff: list[int] | None = None) -> dict:
    """Get the video into out/video.* by the first route that works. Returns yt-dlp style metadata."""
    tried = []
    if any(out.glob("video.*")):  # dropped in by hand or by the desktop job runner
        try:
            return json.loads((out / "info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"webpage_url": url, "id": shortcode(url)}
    routes = [("yt-dlp", lambda: _anonymous(url, out, backoff or []))]
    if cookies and Path(cookies).exists():
        routes.append(("yt-dlp + cookies.txt", lambda: _ytdlp(url, out, cookiefile=cookies)))
    if shortcode(url):  # the other routes only matter for Instagram
        local, roaming = Path(os.environ.get("LOCALAPPDATA", "")), Path(os.environ.get("APPDATA", ""))
        profiles = {"firefox": roaming / "Mozilla/Firefox/Profiles", "edge": local / "Microsoft/Edge/User Data",
                    "chrome": local / "Google/Chrome/User Data"}
        for browser in [b for b, p in profiles.items() if p.exists()]:
            routes.append((f"yt-dlp + {browser} cookies", lambda b=browser: _ytdlp(url, out, cookiesfrombrowser=(b,))))
        routes += [("instaloader", lambda: _instaloader(url, out)), ("embed page", lambda: _embed(url, out))]
    for name, route in routes:
        try:
            meta = route()
            if any(out.glob("video.*")):
                return meta
            tried.append(f"{name}: no file")
        except Exception as exc:
            tried.append(f"{name}: {str(exc).strip().splitlines()[0][:160] if str(exc).strip() else type(exc).__name__}")
        for part in out.glob("video.*"):  # never keep a half-written file from a failed route
            part.unlink(missing_ok=True)
    login = any(re.search(r"empty media|login|cookies", t, re.I) for t in tried)
    print(json.dumps({"error": "every download route failed", "login_only": login, "tried": tried}))
    sys.exit(DOWNLOAD_FAILED)


def main(url: str, out: Path, cookies: str | None = None, backoff: list[int] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    meta = fetch(url, out, cookies, backoff)
    video = next(out.glob("video.*"))
    info = {"url": meta.get("webpage_url") or meta.get("url") or url, "id": meta.get("id"), "title": meta.get("title"),
            "uploader": meta.get("uploader") or meta.get("channel"),
            "caption": meta.get("description") or meta.get("caption") or "",
            "duration": meta.get("duration"), "upload_date": meta.get("upload_date")}

    frames = out / "frames"
    frames.mkdir(exist_ok=True)
    every = 2 if (info["duration"] or 0) <= 180 else 10
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vf", f"fps=1/{every},scale=720:-2",
                    str(frames / "f-%04d.jpg")], check=True)
    for f in sorted(frames.glob("f-*.jpg")):  # name each frame by its time in the video
        f.replace(frames / f"{mmss((int(f.stem[2:]) - 1) * every).replace(':', '-')}.jpg")

    # ffmpeg decodes the audio: the PyAV build faster-whisper installs can't open some files.
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    segments, _ = WhisperModel("small.en", device="cpu", compute_type="int8").transcribe(audio, vad_filter=True)
    lines = [f"[{mmss(s.start)}] {s.text.strip()}" for s in segments]
    (out / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    info.update(frames=len(list(frames.glob("*.jpg"))), frame_every=every, transcript_lines=len(lines))
    (out / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info))


def transcribe(path: str) -> None:
    """`media_worker.py --transcribe <audio file>`: print what was said (for Telegram voice notes)."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    segments, _ = WhisperModel("small.en", device="cpu", compute_type="int8").transcribe(audio, vad_filter=True)
    print(" ".join(s.text.strip() for s in segments))


if __name__ == "__main__":
    if sys.argv[1] == "--transcribe":
        transcribe(sys.argv[2])
    else:
        rest = sys.argv[3:]
        opt = {k: rest[i + 1] for i, k in enumerate(rest[:-1]) if k.startswith("--")}
        main(sys.argv[1], Path(sys.argv[2]), opt.get("--cookies"),
             [int(s) for s in opt.get("--backoff", "").split(",") if s.strip()])
