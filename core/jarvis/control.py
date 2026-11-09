"""The control centre (T98): what the owner used to open the Claude Code window for, served to the dashboard.

- Permission prompts. The dashboard's Claude Code runs with `--permission-prompt-tool stdio`, so anything
  that would have shown a prompt in the Claude Code window (a guarded MCP call the guard hook passes on
  as "ask", a write outside the repo, a question JARVIS asks with AskUserQuestion) arrives here as a
  `can_use_tool` request. Read-only tools are let through (the owner's blanket permission); everything
  else becomes a real approval record (kind "tool"), goes to the phone with Approve / Reject buttons and
  shows on the dashboard, and the answer goes back to Claude Code. Nobody answering means a refusal,
  exactly as before, so the approval gates in CLAUDE.md are unchanged.
- Sessions: the Claude Code conversations of this repo (~/.claude/projects/<repo>/*.jsonl), to list,
  read back and switch to.
- Attachments: any file the owner drops on the talk bar, saved under brain/files/uploads.
- The PC: a screenshot of the desktop, opening apps, folders, files and web pages.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from . import db, journal, paths, records

# ---------------------------------------------------------------- permission prompts

# Tools that only read: answered "allow" without asking, as the Claude Code window is set up to.
READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "Skill", "WebSearch", "WebFetch", "ToolSearch", "TodoWrite",
                   "TaskList", "TaskGet", "ListMcpResourcesTool", "ReadMcpResourceTool"}
YES = re.compile(r"^\s*(yes|yeah|yep|approve[d]?|allow( it)?|go ahead|do it|ok(ay)?|sure|confirm(ed)?)\b[\s.!,sir]*$", re.I)
NO = re.compile(r"^\s*(no|nope|reject(ed)?|deny|denied|don'?t|stop|cancel|refuse)\b[\s.!,sir]*$", re.I)


def describe(tool: str, data: dict) -> str:
    """One line the owner understands: what JARVIS wants to do."""
    data = data if isinstance(data, dict) else {}
    for key in ("command", "file_path", "notebook_path", "url", "query", "description", "prompt"):
        if isinstance(data.get(key), str) and data[key].strip():
            return journal.short(f"{tool}: {data[key]}", 200)
    name = tool.split("__")[-1] if tool.startswith("mcp__") else tool
    server = tool.split("__")[1] if tool.count("__") >= 2 else ""
    return f"{name} on {server}" if server else name


class Permissions:
    """Permission prompts waiting for the owner. `ask` blocks its caller until the owner answers or time runs out."""

    def __init__(self, hub, push=None):
        self.hub = hub
        self.push = push          # sends an approval row to the phone; None when there is no phone
        self.pending: dict[str, dict] = {}
        self.lock = threading.Lock()

    def listing(self) -> list[dict]:
        with self.lock:
            return [{k: v for k, v in p.items() if k != "event"} for p in self.pending.values()]

    def ask(self, request_id: str, req: dict, alive=lambda: True) -> dict:
        """Answer one `can_use_tool` request: returns the response body Claude Code expects."""
        tool = req.get("tool_name") or "?"
        data = req.get("input") if isinstance(req.get("input"), dict) else {}
        if tool in READ_ONLY_TOOLS or tool in (paths.config().get("dashboard_auto_allow") or []):
            return {"behavior": "allow", "updatedInput": data}
        # A question (AskUserQuestion) is only waited on; a prompt to act is a real approval record, because
        # approving one opens the clearance window, as it does from the phone.
        question = tool == "AskUserQuestion"
        summary = (journal.short("JARVIS asks: " + "; ".join(q.get("question", "") for q in data.get("questions") or []), 300)
                   if question else f"Let JARVIS {describe(tool, data)}")
        aid, row = None, None
        if not question:
            conn = db.connect()
            try:
                aid = records.approval_request(conn, "tool", summary, detail=journal.short(
                    json.dumps(data, ensure_ascii=False, default=str), 600))
                row = conn.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone()
            finally:
                conn.close()
        item = {"id": request_id, "approval": aid, "tool": tool, "summary": summary, "input": data,
                "question": question, "since": time.time(), "event": threading.Event(), "answer": None}
        with self.lock:
            self.pending[request_id] = item
        if self.push and row is not None:
            try:
                self.push(row)
            except Exception:  # the phone is a convenience here; the dashboard still shows it
                pass
        self.hub.publish({"type": "permission", **{k: v for k, v in item.items() if k != "event"}})
        deadline = time.time() + float(paths.config().get("permission_wait_seconds", 600))
        status = "pending"
        try:
            while time.time() < deadline and alive():
                if item["event"].wait(1.0):
                    break
                if aid and self._status(aid) != "pending":  # decided on the phone, the board or `bin/jarvis approval`
                    break
            if aid:
                status = self._status(aid)
                if status == "pending":
                    self._decide(aid, False, "no answer in time")
                    status = self._status(aid)
            else:
                status = "answered" if item["answer"] else "unanswered"
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
            self.hub.publish({"type": "permission_done", "id": request_id, "approval": aid, "status": status})
        if question:
            if item["answer"]:
                return {"behavior": "allow", "updatedInput": {**data, "answers": item["answer"]}}
            return {"behavior": "deny", "message": "The owner didn't answer the question. Carry on with your best judgement "
                                                   "where it's safe, or ask again in your reply."}
        if status == "approved":
            return {"behavior": "allow", "updatedInput": data}
        why = ("The owner rejected A{0}. Don't retry it; ask what they want instead." if status == "rejected"
               else "Nobody answered A{0} in time, so it was refused. Tell the owner what you wanted to do.")
        return {"behavior": "deny", "message": why.format(aid)}

    def answer(self, request_id: str | None, approve: bool | None = None, answers: dict | None = None) -> dict:
        """The owner answered on the dashboard (or by voice): `approve` for a prompt, `answers` for a question.
        No id: the oldest waiting prompt."""
        with self.lock:
            item = self.pending.get(request_id) if request_id else next(iter(self.pending.values()), None)
        if not item:
            raise ValueError("Nothing is waiting for an answer.")
        if item["question"]:
            if not answers or not isinstance(answers, dict):
                raise ValueError("Pick an answer first.")
            item["answer"] = {str(k): str(v) for k, v in answers.items()}
            status = "answered"
        else:
            self._decide(item["approval"], bool(approve), "decided on the dashboard")
            status = self._status(item["approval"])
        item["event"].set()
        return {"id": item["id"], "approval": item["approval"], "status": status}

    def voice(self, text: str) -> dict | None:
        """A spoken or typed "yes" / "no" while a prompt is waiting answers it, instead of going to JARVIS."""
        with self.lock:
            waiting = [p for p in self.pending.values() if not p["question"]]
        if not waiting:
            return None
        if YES.match(text or ""):
            return self.answer(waiting[0]["id"], True)
        if NO.match(text or ""):
            return self.answer(waiting[0]["id"], False)
        return None

    @staticmethod
    def _status(aid: int) -> str:
        conn = db.connect()
        try:
            return conn.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()[0]
        finally:
            conn.close()

    @staticmethod
    def _decide(aid: int, approve: bool, note: str) -> None:
        conn = db.connect()
        try:
            records.approval_decide(conn, aid, approve, note)
        except records.RecordError:
            pass  # someone else got there first
        finally:
            conn.close()


# ---------------------------------------------------------------- sessions

SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_titles: dict[str, tuple[float, dict]] = {}


def sessions_dir() -> Path:
    """Where Claude Code keeps this repo's conversations: the path with every other character made a dash."""
    override = paths.config().get("claude_sessions_dir")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(paths.REPO_ROOT))


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _owner_text(ev: dict) -> str:
    """The owner's words in a `user` line, or "" for tool results, hook output and system notes."""
    if ev.get("type") != "user" or ev.get("isMeta") or ev.get("isSidechain"):
        return ""
    text = _text_of((ev.get("message") or {}).get("content")).strip()
    if not text or text.startswith("<") or text.startswith("Caveat:"):
        return ""
    return text


def _head(path: Path) -> dict:
    """Title (the first thing the owner said, or a saved title) without reading a long file to the end."""
    title, first_ts = "", None
    with path.open(encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if n > 400:
                break
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") in ("custom-title", "summary") and (ev.get("customTitle") or ev.get("summary")):
                title = ev.get("customTitle") or ev.get("summary")
                break
            first_ts = first_ts or ev.get("timestamp")
            if not title and _owner_text(ev):
                title = _owner_text(ev)
                break
    return {"title": journal.short(" ".join(title.split()), 120) or "(no messages)", "started": first_ts}


def sessions(limit: int = 30, current: str | None = None) -> list[dict]:
    folder = sessions_dir()
    if not folder.is_dir():
        return []
    files = sorted((p for p in folder.glob("*.jsonl") if SESSION_ID.match(p.stem)), key=lambda p: p.stat().st_mtime,
                   reverse=True)[:limit]
    out = []
    for p in files:
        st = p.stat()
        cached = _titles.get(p.stem)
        if not cached or cached[0] != st.st_mtime:
            try:
                cached = (st.st_mtime, _head(p))
            except OSError:
                continue
            _titles[p.stem] = cached
        out.append({"id": p.stem, **cached[1], "updated": st.st_mtime, "kb": st.st_size // 1024,
                    "current": p.stem == current})
    return out


def transcript(sid: str, limit: int = 200) -> list[dict]:
    """A conversation read back: the owner's messages, JARVIS's text and the tools he ran, oldest first."""
    if not SESSION_ID.match(sid or ""):
        raise ValueError("Not a session id.")
    path = sessions_dir() / f"{sid}.jsonl"
    if not path.exists():
        raise ValueError("No such conversation.")
    out: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("isSidechain"):
                continue
            ts = ev.get("timestamp")
            said = _owner_text(ev)
            if said:
                out.append({"role": "owner", "text": said, "ts": ts})
            elif ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        out.append({"role": "jarvis", "text": b["text"], "ts": ts})
                    elif b.get("type") == "tool_use":
                        out.append({"role": "tool", "text": describe(b.get("name", ""), b.get("input") or {}), "ts": ts})
    return out[-limit:]


# ---------------------------------------------------------------- attachments

MAX_FILES, MAX_FILE_BYTES = 4, 15 * 1024 * 1024
BLOCKED_SUFFIXES = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".scr", ".lnk",
                    ".dll", ".reg", ".hta", ".cpl", ".jar", ".psm1", ".sh"}


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name or "file").name).strip(" .") or "file"
    return stem[-100:]


def save_files(files: list) -> list[str]:
    """Save attached files (any kind but programs) under brain/files/uploads; returns their brain paths."""
    if not isinstance(files, list):
        raise ValueError("files must be a list.")
    if len(files) > MAX_FILES:
        raise ValueError(f"At most {MAX_FILES} files per message.")
    folder = paths.files_dir() / "uploads"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = []
    for f in files:
        name = safe_name((f or {}).get("name") or "file")
        if Path(name).suffix.lower() in BLOCKED_SUFFIXES:
            raise ValueError(f"{name}: programs and scripts can't be attached.")
        try:
            data = base64.b64decode((f or {}).get("data") or "", validate=True)
        except (ValueError, TypeError):
            raise ValueError(f"{name} couldn't be read.")
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{name}: each file must be under 15 MB.")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stamp}-{name}"
        path.write_bytes(data)
        saved.append(paths.rel(path))
    return saved


# ---------------------------------------------------------------- the PC

# Apps the dashboard can open by name. brain/config.json "pc_apps" adds more ({"name": "command or path"}).
APPS = {
    "explorer": ["explorer.exe"], "files": ["explorer.exe"], "notepad": ["notepad.exe"], "calculator": ["calc.exe"],
    "terminal": ["wt.exe"], "powershell": ["powershell.exe"], "chrome": ["cmd", "/c", "start", "", "chrome"],
    "edge": ["cmd", "/c", "start", "", "msedge"], "vscode": ["cmd", "/c", "code"], "claude": ["cmd", "/c", "start", "", "claude:"],
    "dicloak": ["cmd", "/c", "start", "", "DICloak"], "settings": ["cmd", "/c", "start", "", "ms-settings:"],
    "task manager": ["taskmgr.exe"],
}
EXEC_SUFFIXES = BLOCKED_SUFFIXES - {".js", ".sh"}


def apps() -> dict:
    found = {k: v for k, v in APPS.items()}
    for name, cmd in (paths.config().get("pc_apps") or {}).items():
        found[str(name).lower()] = cmd if isinstance(cmd, list) else [cmd]
    return found


def resolve(target: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(target.strip().strip('"'))))
    return p if p.is_absolute() else paths.REPO_ROOT / p


def open_target(target: str, launch=None) -> str:
    """Open a web page, an app by name, a folder or a file (not a program) on this PC. Returns what was opened."""
    target = (target or "").strip()
    if not target:
        raise ValueError("Say what to open.")
    launch = launch or (lambda cmd: subprocess.Popen(cmd, cwd=str(Path.home()), creationflags=paths.NO_WINDOW,
                                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.DEVNULL))
    if re.match(r"^https?://", target, re.I):
        webbrowser.open(target)
        return f"web page {target}"
    known = apps()
    if target.lower() in known:
        launch(known[target.lower()])
        return f"app {target}"
    path = resolve(target)
    if not path.exists():
        raise ValueError(f"I can't find {target}: not an app I know ({', '.join(sorted(known))}), a web address or a path.")
    if path.is_dir():
        launch(["explorer.exe", str(path)])
        return f"folder {paths.rel(path) if str(path).startswith(str(paths.REPO_ROOT)) else path}"
    if path.suffix.lower() in EXEC_SUFFIXES:
        raise ValueError("Programs can't be started from here; add them to pc_apps in brain/config.json.")
    if os.name == "nt":
        os.startfile(str(path))  # its usual app
    else:
        launch(["xdg-open", str(path)])
    return f"file {path.name}"


_shot = {"at": 0.0, "jpg": b"", "lock": threading.Lock()}


class ScreenError(Exception):
    pass


def screenshot(width: int = 1600, max_age: float = 1.5) -> bytes:
    """The whole desktop as a JPEG (Windows), at most `width` wide. Shared by every viewer for `max_age` seconds."""
    if os.name != "nt":
        raise ScreenError("Screen view needs Windows.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ScreenError("Screen view needs ffmpeg (it grabs the desktop with gdigrab).")
    with _shot["lock"]:
        if _shot["jpg"] and time.time() - _shot["at"] < max_age:
            return _shot["jpg"]
        width = max(320, min(int(width), 3840))
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "gdigrab", "-i", "desktop",
                                "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "5", "-f", "mjpeg", "-"],
                               capture_output=True, timeout=20, creationflags=paths.NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScreenError(f"Couldn't take a screenshot: {exc}")
        jpg = r.stdout if r.stdout.startswith(bytes([0xFF, 0xD8])) else b""  # a JPEG, not an error message
        if not jpg:
            err = r.stderr.decode("utf-8", "replace").strip()[:200]
            raise ScreenError("Couldn't take a screenshot" + (f": {err}" if err else "."))
        _shot.update(at=time.time(), jpg=jpg)
        return jpg


# ---------------------------------------------------------------- HTTP (the dashboard calls these)

def _q(q: dict, key: str, default: str = "") -> str:
    return (q.get(key) or [default])[0]


def handle_get(route: str, q: dict, server) -> tuple[int, object, str] | None:
    """GET /api/control/*: (status, body, content type) or None when the route isn't ours.
    `server` carries `runner` (with `permissions`, `queue`, `commands`) and `token`."""
    from . import jobs, work
    runner = server.runner
    if route == "/api/control/state":
        conn = db.connect()
        try:
            job_rows = [dict(r) for r in jobs.listing(conn, 12)]
        finally:
            conn.close()
        return 200, {"permissions": runner.permissions.listing(), "queue": list(runner.queue), "busy": runner.busy,
                     "status": runner.live.get("status"), "steps": runner.steps[-60:], "session": runner.session(),
                     "commands": runner.commands, "jobs": job_rows, "job_runner": jobs.runner_alive(),
                     "workers": work.listing(15), "apps": sorted(apps())}, "json"
    if route == "/api/control/sessions":
        return 200, sessions(int(_q(q, "n", "30") or 30), runner.session()), "json"
    if route == "/api/control/transcript":
        try:
            return 200, transcript(_q(q, "id")), "json"
        except ValueError as exc:
            return 404, {"error": str(exc)}, "json"
    if route == "/api/control/worker":
        try:
            return 200, work.tail(_q(q, "id"), int(_q(q, "n", "120") or 120)), "json"
        except (work.WorkError, ValueError) as exc:
            return 404, {"error": str(exc)}, "json"
    if route == "/api/control/screen":
        # The PC's screen is private: the page key goes in the query, since an <img> can't send a header.
        if _q(q, "k") != server.token:
            return 403, {"error": "forbidden"}, "json"
        try:
            return 200, screenshot(int(_q(q, "w", "1600") or 1600)), "image/jpeg"
        except (ScreenError, ValueError) as exc:
            return 503, {"error": str(exc)}, "json"
    return None


def handle_post(path: str, body: dict, server) -> tuple[int, object] | None:
    """POST /api/control/* (already checked for the page key): (status, body) or None."""
    from . import jobs, work
    runner = server.runner
    try:
        if path == "/api/control/permission":
            return 200, runner.permissions.answer(body.get("id"), body.get("approve"), body.get("answers"))
        if path == "/api/control/session":
            sid = (body.get("id") or "").strip()
            if sid and not SESSION_ID.match(sid):
                raise ValueError("Not a session id.")
            if runner.busy:
                return 409, {"error": "JARVIS is still answering; stop him first."}
            runner.switch(sid or None)
            return 200, {"ok": True, "session": sid or None}
        if path == "/api/control/open":
            return 200, {"ok": True, "opened": open_target(str(body.get("target") or ""))}
        if path == "/api/control/job":
            conn = db.connect()
            try:
                if body.get("cancel"):
                    return 200, dict(jobs.cancel(conn, body["cancel"]))
                jid = jobs.add(conn, str(body.get("text") or ""), source="dashboard")
                return 201, {"ok": True, "id": jid, "runner": jobs.runner_alive()}
            finally:
                conn.close()
        if path == "/api/control/worker":
            action = body.get("action")
            if action == "start":
                return 201, work.start(str(body.get("brief") or ""), task=str(body.get("task") or ""),
                                       agent=str(body.get("agent") or ""))
            if action == "stop":
                return 200, work.stop(body.get("id"))
            if action == "resume":
                return 200, {"resumed": [r["id"] for r in work.resume(body.get("id"))]}
            raise ValueError("action must be start, stop or resume.")
        if path == "/api/control/unqueue":
            runner.unqueue(int(body.get("index", -1)))
            return 200, {"queue": list(runner.queue)}
    except (ValueError, TypeError, KeyError, jobs.JobError, work.WorkError) as exc:
        return 400, {"error": str(exc)}
    return None
