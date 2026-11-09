"""Stands in for media_worker.py in tests: no download, no Whisper.

Links whose id is listed in FAKE_MEDIA_FAIL fail the way the real worker does when every download
route is refused (a JSON line and exit 3). Others "download": video.mp4, six frames, transcript.md and
info.json are written.
"""

import json
import os
import sys
from pathlib import Path

url, out = sys.argv[1], Path(sys.argv[2])
key = out.name
if key in (os.environ.get("FAKE_MEDIA_FAIL") or "").split(","):
    print(json.dumps({"error": "every download route failed", "login_only": True,
                      "tried": ["yt-dlp: Instagram sent an empty media response", "instaloader: Fetching Post metadata failed."]}))
    sys.exit(3)
out.mkdir(parents=True, exist_ok=True)
(out / "video.mp4").write_bytes(b"fake video")
(out / "frames").mkdir(exist_ok=True)
for s in range(0, 12, 2):
    (out / "frames" / f"00-{s:02d}.jpg").write_bytes(b"fake frame")
(out / "transcript.md").write_text("[00:00] Test hook line.\n", encoding="utf-8")
info = {"url": url, "id": key, "title": None, "uploader": "tester", "caption": f"Caption of {key}",
        "duration": 12, "upload_date": "20261109", "frames": 6, "frame_every": 2, "transcript_lines": 1,
        "args": sys.argv[3:]}
(out / "info.json").write_text(json.dumps(info), encoding="utf-8")
print(json.dumps(info))
