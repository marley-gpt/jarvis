"""Watch one video: download it, transcribe every word, save the frames. Runs inside the media venv.

    <media-venv python> media_worker.py <url> <out-dir>

Writes video.mp4, info.json (title, uploader, caption, duration, link), transcript.md
([mm:ss] lines, local Whisper) and frames/mm-ss.jpg (every 2 s for short videos,
every 10 s for long ones), then prints info.json's contents. Needs yt-dlp,
faster-whisper and ffmpeg (`bin/jarvis watch --setup` installs the first two).
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import yt_dlp
from faster_whisper import WhisperModel


def mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def main(url: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    opts = {"quiet": True, "no_warnings": True, "outtmpl": str(out / "video.%(ext)s"),
            "format": "b[height<=720]/bv*[height<=720]+ba/b", "merge_output_format": "mp4"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        meta = ydl.extract_info(url, download=True)
    video = next(out.glob("video.*"))
    info = {"url": meta.get("webpage_url") or url, "id": meta.get("id"), "title": meta.get("title"),
            "uploader": meta.get("uploader") or meta.get("channel"), "caption": meta.get("description") or "",
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
        main(sys.argv[1], Path(sys.argv[2]))
