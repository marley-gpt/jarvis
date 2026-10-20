"""Receive mentor lessons captured in the owner's logged-in browser and file them in the brain.

The owner's Skool lessons are watched in their own Chrome (Claude in Chrome runs
core/jarvis/skool_capture.js inside the lesson page). The page reads what the
owner can already see — lesson text, resources, the video's caption track and
video frames — and posts it here. Nothing is downloaded from Skool by this
process; it only accepts uploads.

`bin/jarvis mentor serve` listens on 127.0.0.1 only, accepts requests only from
https://www.skool.com carrying the per-run token (written to
brain/scratch/mentor-capture.json for the agent to read), and writes under
brain/files/mentor/skool/<course>/<NN-lesson>/:

  lesson.json      metadata, description text, resources, image list
  transcript.md    timed captions, one line per cue ([mm:ss] text)
  frames/          video frames as mm-ss.jpg (deduplicated scene changes)
  images/          images embedded in the lesson text
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths

ALLOWED_ORIGINS = ("https://www.skool.com", "https://skool.com")
MAX_BODY = 25 * 1024 * 1024
DEFAULT_PORT = 8766
SCRIPT = Path(__file__).with_name("skool_capture.js")
# What the agent runs in each lesson page (fill in port and token from info_path()):
LOADER = ('const CAPTURE = {{port: {port}, token: "{token}"}}; '
          'eval(await (await fetch(`http://127.0.0.1:${{CAPTURE.port}}/script`, '
          '{{headers: {{"X-Capture-Token": CAPTURE.token}}}})).text()); ')


def slug(text: str, n: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n].rstrip("-")) or "untitled"


def root() -> Path:
    return paths.files_dir() / "mentor" / "skool"


def lesson_dir(course_order: int, course_title: str, lesson_order: int, lesson_title: str) -> Path:
    course = f"{int(course_order):02d}-{slug(course_title, 40)}"
    lesson = f"{int(lesson_order):02d}-{slug(lesson_title)}"
    return root() / course / lesson


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def save_lesson(data: dict) -> Path:
    d = lesson_dir(data["course_order"], data["course_title"], data["lesson_order"], data["lesson_title"])
    d.mkdir(parents=True, exist_ok=True)
    cues = data.pop("cues", None) or []
    meta = {k: v for k, v in data.items() if k not in ("token",)}
    meta["has_transcript"] = bool(cues)
    (d / "lesson.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    if cues:
        lines = [f"# {data['lesson_title']}", "",
                 f"Course: {data['course_title']} · Source: Skool video captions (auto-generated, may mishear words)", ""]
        lines += [f"[{mmss(t)}] {text}" for t, text in cues]
        (d / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def save_image(data: dict, kind: str) -> Path:
    d = lesson_dir(data["course_order"], data["course_title"], data["lesson_order"], data["lesson_title"])
    sub = d / ("frames" if kind == "frame" else "images")
    sub.mkdir(parents=True, exist_ok=True)
    if kind == "frame":
        name = mmss(float(data["t"])).replace(":", "-") + ".jpg"
    else:
        name = f"{int(data.get('index', 0)):02d}-{slug(data.get('name', 'image'), 40)}.jpg"
    raw = data["data"].split(",", 1)[-1]
    target = sub / name
    target.write_bytes(base64.b64decode(raw))
    return target


def captured() -> list[str]:
    base = root()
    if not base.exists():
        return []
    return sorted(str(p.parent.relative_to(base)).replace("\\", "/") for p in base.glob("*/*/lesson.json"))


def make_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT, token: str | None = None) -> ThreadingHTTPServer:
    token = token or secrets.token_urlsafe(18)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _cors(self) -> bool:
            origin = self.headers.get("Origin", "")
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Capture-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                return True
            return False

        def _reply(self, code: int, data) -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_address[1]}",
                                                    f"localhost:{self.server.server_address[1]}")

        def _allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return (self._host_ok() and self.headers.get("X-Capture-Token") == token
                    and (origin is None or origin in ALLOWED_ORIGINS))

        def do_OPTIONS(self):
            self.send_response(204 if self.headers.get("Origin", "") in ALLOWED_ORIGINS else 403)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._allowed():
                return self._reply(403, {"error": "forbidden"})
            if self.path == "/status":
                return self._reply(200, {"captured": captured()})
            if self.path == "/script":
                body = SCRIPT.read_bytes()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            self._reply(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._reply(413, {"error": "too large"})
            raw = self.rfile.read(length)
            if not self._allowed():
                return self._reply(403, {"error": "forbidden"})
            try:
                data = json.loads(raw or b"{}")
                for key in ("course_order", "course_title", "lesson_order", "lesson_title"):
                    if key not in data:
                        raise ValueError(f"missing {key}")
                with lock:
                    if self.path == "/lesson":
                        where = save_lesson(data)
                    elif self.path in ("/frame", "/image"):
                        where = save_image(data, self.path[1:])
                    else:
                        return self._reply(404, {"error": "not found"})
            except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
                return self._reply(400, {"error": str(exc)})
            self._reply(200, {"ok": True, "path": paths.rel(where)})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    server = Server((host, port), Handler)
    server.token = token
    return server


def info_path() -> Path:
    return paths.home() / "scratch" / "mentor-capture.json"


def serve(port: int = DEFAULT_PORT) -> None:
    server = make_server(port=port)
    p = info_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    port = server.server_address[1]
    p.write_text(json.dumps({"port": port, "token": server.token,
                             "loader": LOADER.format(port=port, token=server.token)}), encoding="utf-8")
    print(f"Mentor capture receiver on http://127.0.0.1:{server.server_address[1]} "
          f"(token in {paths.rel(p)}). Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        p.unlink(missing_ok=True)
