"""Live local dashboard: watch JARVIS delegate to its agents, and talk to it.

`bin/jarvis dashboard` serves the pages in core/dashboard/ on 127.0.0.1 (the
isometric office at `/`, the sidebar console at `/console`, the first version at
`/classic`, all sharing jarvis.js) and streams
state over Server-Sent Events. Talking to JARVIS runs Claude Code headless
(`claude -p --output-format stream-json`), resuming one conversation, so it uses
the owner's existing Claude subscription. Each delegation (Agent tool call) and
each specialist's tool use is pushed to the page as it happens. Durable state
(tasks, approvals, memory, journal) comes from the brain, so the page also shows
work done from any other Claude Code session.

Voice is handled in the browser (Web Speech API for listening and speaking), so
it costs nothing extra.
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from . import brief, db, journal, memory, paths, records, tts

STATIC = paths.CORE_DIR / "dashboard"
# The office is the main view (owner decision D1); the console and the first version stay available.
PAGES = {"/": "office.html", "/console": "console.html", "/office": "office.html", "/classic": "index.html"}
ASSETS = {"/jarvis.js": ("jarvis.js", "text/javascript; charset=utf-8")}
AGENTS_DIR = paths.REPO_ROOT / ".claude" / "agents"

# Display order and short names; mentor first because every other agent depends on it.
AGENT_LABELS = {
    "mentor-learning": "Mentor Notes",
    "product-research": "Product Research",
    "market-research": "Customer & Market",
    "creative-research": "Creative Research",
    "creative-director": "Creative Director",
    "shopify": "Shopify",
    "meta-ads": "Meta Ads",
    "meta-ads-auditor": "Meta Auditor",
    "product-testing": "Product Testing",
    "operations": "Operations",
}

# Kept free of cmd.exe metacharacters: on Windows `claude` is often a .cmd shim.
VOICE_PROMPT = (
    "The owner is talking to you through the JARVIS dashboard, often by voice, and your replies are read aloud "
    "in a British voice. Speak as JARVIS from the films: a calm, courteous British butler and engineer, "
    "unflappable, precise and quietly capable, with a dry and understated wit used lightly. "
    "Use British spelling and phrasing. You may address the owner as sir now and then, but sparingly, "
    "never in every reply. Be brief: a few short spoken sentences, no tables, no code blocks, no headings, "
    "no lists, no markdown. Lead with the answer, then the one thing that matters next. "
    "When you hand work to specialists, say plainly who is doing what. Put detail in deliverable files, "
    "not in the spoken reply. Never invent figures or results. Consequential actions such as spending, "
    "launching or changing ads, prices or the live store still need the owner's approval first."
)


# Claude Code's own wording when it has no credentials, and when a resumed conversation is gone.
LOGIN_PROBLEM = re.compile(r"^\s*(not logged in|invalid api key|please run /login)", re.I)
MISSING_SESSION = re.compile(r"no conversation found", re.I)
LOGIN_HELP = ("I can't answer yet because Claude Code isn't signed in on this computer. "
              "Open a terminal, run claude once and sign in with your Claude account, then talk to me again.")


# ---------------------------------------------------------------- state

def agent_roster() -> list[dict]:
    found = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        meta, _ = memory.parse(path.read_text(encoding="utf-8"))
        name = meta.get("name") or path.stem
        desc = meta.get("description", "")
        role = re.split(r"(?<=\.)\s", desc, maxsplit=1)
        found[name] = {
            "name": name,
            "label": AGENT_LABELS.get(name, name.replace("-", " ").title()),
            "role": role[1] if len(role) > 1 else desc,
            "title": role[0].rstrip("."),
            "model": meta.get("model", "inherit"),
        }
    order = [n for n in AGENT_LABELS if n in found] + [n for n in found if n not in AGENT_LABELS]
    return [found[n] for n in order]


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


def snapshot(conn, live: dict | None = None) -> dict:
    live = live or {}
    agents = agent_roster()
    for a in agents:
        a["tasks"] = _rows(records.task_list(conn, agent=a["name"], open_only=True, limit=5))
        last = conn.execute("SELECT * FROM tasks WHERE agent = ? AND status = 'done' ORDER BY completed DESC LIMIT 1",
                            (a["name"],)).fetchone()
        a["last_done"] = dict(last) if last else None
        run = conn.execute("SELECT * FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT 1", (a["name"],)).fetchone()
        running = bool(run and run["event"] == "SubagentStart" and time.time() - _epoch(run["ts"]) < 3 * 3600)
        live_agent = live.get("agents", {}).get(a["name"])
        if live_agent:
            a["status"], a["now"] = "working", live_agent.get("description")
        elif running or any(t["status"] == "in_progress" for t in a["tasks"]):
            a["status"], a["now"] = "working", next((t["title"] for t in a["tasks"] if t["status"] == "in_progress"), None)
        elif a["tasks"]:
            a["status"], a["now"] = "queued", a["tasks"][0]["title"]
        else:
            a["status"], a["now"] = "idle", None
        a["activity"] = live.get("activity", {}).get(a["name"], [])[-4:]
        a["runs"] = conn.execute("SELECT COUNT(*) FROM agent_runs WHERE agent = ? AND event = 'SubagentStart'",
                                 (a["name"],)).fetchone()[0]
        a["open"] = len(a["tasks"])

    parents = conn.execute(
        f"""SELECT * FROM tasks WHERE parent_id IS NULL AND status IN ({','.join('?' * len(records.OPEN_TASK_STATUSES))})
            ORDER BY updated DESC LIMIT 8""", records.OPEN_TASK_STATUSES).fetchall()
    tasks = []
    for p in parents:
        subs = conn.execute("SELECT id, title, agent, status FROM tasks WHERE parent_id = ? ORDER BY id", (p["id"],)).fetchall()
        tasks.append({**dict(p), "subtasks": _rows(subs)})

    recent_tasks = conn.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT 60").fetchall()
    products = conn.execute("SELECT * FROM products ORDER BY updated DESC").fetchall()
    tests = []
    for t in conn.execute("SELECT * FROM tests ORDER BY id DESC LIMIT 40").fetchall():
        tests.append({**dict(t), "eval": records.evaluate(conn, t["id"])})
    month = paths.now()[:7]
    by_service = _rows(conn.execute("SELECT service, ROUND(SUM(amount_aud), 2) AS aud, COUNT(*) AS n FROM costs "
                                    "WHERE substr(ts, 1, 7) = ? GROUP BY service ORDER BY aud DESC", (month,)))
    counts = {
        "delegations": conn.execute("SELECT COUNT(*) FROM tasks WHERE parent_id IS NOT NULL").fetchone()[0],
        "decisions": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
        "open_tasks": conn.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({','.join('?' * len(records.OPEN_TASK_STATUSES))})",
                                   records.OPEN_TASK_STATUSES).fetchone()[0],
        "agent_runs": conn.execute("SELECT COUNT(*) FROM agent_runs WHERE event = 'SubagentStart'").fetchone()[0],
    }
    events = conn.execute(
        """SELECT e.ts, e.kind, e.note, t.id AS task_id, t.title, t.agent FROM task_events e
           JOIN tasks t ON t.id = e.task_id ORDER BY e.id DESC LIMIT 25""").fetchall()
    ok, spent, budget = records.cost_check(conn, 0)
    st = memory.stats(conn)
    return {
        "agents": agents,
        "tasks": tasks,
        "approvals": _rows(conn.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id")),
        "activity": _rows(events),
        "memory": {"total": st["total"], "needs_review": st["needs_review"], "by_category": st["by_category"],
                   "recent": _rows(memory.listing(conn, status="active", limit=6))},
        "journal": _rows(journal.recent(conn, limit=40)),
        "cost": {"spent": spent, "budget": budget, "by_service": by_service},
        "recent_tasks": _rows(recent_tasks),
        "products": _rows(products),
        "tests": tests,
        "counts": counts,
        "brain": str(paths.home()),
        "jarvis": {"status": live.get("status", "idle"), "request": live.get("request"),
                   "claude": live.get("claude"), "login_needed": bool(db.get_meta(conn, "claude_login_needed"))},
    }


def _epoch(ts: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


def signature(conn) -> tuple:
    """Cheap fingerprint of the brain: changes whenever anything the page shows changes."""
    parts = []
    for table in ("tasks", "task_events", "agent_runs", "journal", "approvals", "decisions", "memory", "costs",
                  "products", "tests", "test_metrics"):
        parts.append(conn.execute(f"SELECT COUNT(*), MAX(rowid) FROM {table}").fetchone()[:])
    parts.append(conn.execute("SELECT MAX(updated) FROM tasks").fetchone()[0])
    parts.append(conn.execute("SELECT MAX(updated) FROM memory").fetchone()[0])
    parts.append(conn.execute("SELECT MAX(updated) FROM products").fetchone()[0])
    parts.append(db.get_meta(conn, "claude_login_needed"))
    parts.append(conn.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0])
    parts.append(conn.execute("SELECT GROUP_CONCAT(status) FROM tests").fetchone()[0])
    return tuple(parts)


# ---------------------------------------------------------------- live events

class Hub:
    """Fan-out of live events to every open page."""

    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, event: dict) -> None:
        with self.lock:
            for q in self.subs:
                q.put(event)


def find_claude() -> list[str] | None:
    cfg = paths.config().get("claude_command")
    if cfg:
        return cfg if isinstance(cfg, list) else [cfg]
    exe = shutil.which("claude")
    if not exe:
        # The native installer puts it here but doesn't always add the folder to PATH.
        native = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        exe = str(native) if native.exists() else None
    return [exe] if exe else None


def _summary(tool: str, data: dict) -> str:
    for key in ("description", "command", "file_path", "pattern", "query", "url", "prompt"):
        if isinstance(data.get(key), str):
            return journal.short(f"{tool}: {data[key]}", 110)
    return tool


class Runner:
    """One conversation with JARVIS through headless Claude Code, one message at a time."""

    def __init__(self, hub: Hub):
        self.hub = hub
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.live: dict = {"status": "idle", "agents": {}, "activity": {}}

    @property
    def busy(self) -> bool:
        return self.live["status"] != "idle"

    def session(self) -> str | None:
        conn = db.connect()
        try:
            return db.get_meta(conn, "dashboard_session")
        finally:
            conn.close()

    def reset(self, announce: bool = True) -> None:
        conn = db.connect()
        try:
            db.set_meta(conn, "dashboard_session", "")
        finally:
            conn.close()
        if announce:
            self.hub.publish({"type": "reset"})

    def start(self, text: str) -> None:
        with self.lock:
            if self.busy:
                raise RuntimeError("JARVIS is still answering the last message.")
            claude = find_claude()
            if not claude:
                raise FileNotFoundError(
                    "Claude Code CLI not found. Install it (it uses your existing Claude subscription), "
                    "or set \"claude_command\" in brain/config.json.")
            self.live = {"status": "thinking", "request": text, "agents": {}, "activity": {}}
        self.hub.publish({"type": "status", "status": "thinking", "request": text})
        threading.Thread(target=self._run, args=(claude, text), daemon=True).start()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _run(self, claude: list[str], text: str) -> None:
        try:
            sid = self.session()
            got_result, err = self._attempt(claude, text, sid)
            if not got_result and sid and MISSING_SESSION.search(err):
                # The saved conversation is gone (e.g. cleared by Claude Code): start a fresh one.
                self.reset(announce=False)
                got_result, err = self._attempt(claude, text, None)
            if not got_result:
                self.hub.publish({"type": "error", "text": journal.short(err or "JARVIS stopped without answering.", 400)})
        except OSError as exc:
            self.hub.publish({"type": "error", "text": f"Could not start Claude Code: {exc}"})
        finally:
            self.proc = None
            self.live = {"status": "idle", "agents": {}, "activity": self.live.get("activity", {})}
            self.hub.publish({"type": "status", "status": "idle"})

    def _attempt(self, claude: list[str], text: str, sid: str | None) -> tuple[bool, str]:
        cmd = [*claude, "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits", "--append-system-prompt", VOICE_PROMPT]
        if sid:
            cmd += ["--resume", sid]
        dispatched: dict[str, str] = {}
        got_result = False
        proc = self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, encoding="utf-8", errors="replace", cwd=paths.REPO_ROOT)
        try:
            proc.stdin.write(text)
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                got_result |= self._handle(ev, dispatched)
            proc.wait()
            return got_result, ("" if got_result else (proc.stderr.read() or "").strip())
        finally:
            proc.stdout.close()
            proc.stderr.close()

    def _login_needed(self, needed: bool) -> None:
        conn = db.connect()
        try:
            if bool(db.get_meta(conn, "claude_login_needed")) != needed:
                db.set_meta(conn, "claude_login_needed", "1" if needed else "")
        finally:
            conn.close()

    def _handle(self, ev: dict, dispatched: dict) -> bool:
        kind = ev.get("type")
        if kind == "result":
            text = ev.get("result") or ""
            if ev.get("is_error") and LOGIN_PROBLEM.search(text):
                self._login_needed(True)
                self.hub.publish({"type": "reply", "text": LOGIN_HELP, "is_error": True, "login": True})
                return True
            self._login_needed(False)
        if ev.get("session_id") and kind in ("system", "result"):
            conn = db.connect()
            try:
                db.set_meta(conn, "dashboard_session", ev["session_id"])
            finally:
                conn.close()
        parent = ev.get("parent_tool_use_id")
        content = (ev.get("message") or {}).get("content")
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    if not parent and LOGIN_PROBLEM.search(block["text"]):
                        continue  # reported once, in plain words, with the result
                    if parent and parent in dispatched:
                        self._agent_line(dispatched[parent], journal.short(block["text"], 110))
                    elif not parent:
                        self.hub.publish({"type": "say", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    name, data = block.get("name", ""), block.get("input") or {}
                    if name in ("Agent", "Task"):
                        agent = data.get("subagent_type") or "general-purpose"
                        dispatched[block.get("id")] = agent
                        self.live["agents"][agent] = {"description": data.get("description")}
                        self.live["status"] = "delegating"
                        self.hub.publish({"type": "dispatch", "agent": agent, "description": data.get("description"),
                                          "id": block.get("id")})
                    elif parent and parent in dispatched:
                        self._agent_line(dispatched[parent], _summary(name, data))
                    elif not parent:
                        self.hub.publish({"type": "jarvis_tool", "summary": _summary(name, data)})
        elif kind == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    agent = dispatched.pop(block.get("tool_use_id"), None)
                    if agent:
                        self.live["agents"].pop(agent, None)
                        if not self.live["agents"]:
                            self.live["status"] = "thinking"
                        self.hub.publish({"type": "agent_done", "agent": agent, "id": block.get("tool_use_id")})
        elif kind == "result":
            self.hub.publish({"type": "reply", "text": ev.get("result") or "", "is_error": bool(ev.get("is_error")),
                              "cost_usd": ev.get("total_cost_usd")})
            return True
        return False

    def _agent_line(self, agent: str, line: str) -> None:
        self.live["activity"].setdefault(agent, []).append(line)
        self.live["activity"][agent] = self.live["activity"][agent][-6:]
        self.hub.publish({"type": "agent_activity", "agent": agent, "line": line})


# ---------------------------------------------------------------- HTTP

def make_server(host="127.0.0.1", port=8765) -> ThreadingHTTPServer:
    hub = Hub()
    runner = Runner(hub)
    kokoro = tts.Kokoro()
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console quiet
            pass

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding: only requests addressed to this machine by loopback name are served.
            port_ = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_}", f"localhost:{port_}")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, default=str).encode(), "application/json")

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "forbidden host"})
            route, _, query = self.path.partition("?")
            if route in PAGES:
                html = (STATIC / PAGES[route]).read_text(encoding="utf-8").replace("__JARVIS_TOKEN__", token)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if route in ASSETS:
                name, ctype = ASSETS[route]
                return self._send(200, (STATIC / name).read_bytes(), ctype)
            if route == "/api/memory":
                return self._memory(parse_qs(query))
            if route.startswith("/api/memory/"):
                conn = db.connect()
                try:
                    entry = memory.get(conn, unquote(route.rsplit("/", 1)[1]))
                except memory.MemoryError as exc:
                    return self._json(404, {"error": str(exc)})
                finally:
                    conn.close()
                return self._json(200, {"meta": entry.meta, "body": entry.body, "path": paths.rel(entry.path)})
            if self.path == "/api/state":
                conn = db.connect()
                try:
                    data = snapshot(conn, runner.live)
                finally:
                    conn.close()
                data["jarvis"]["claude"] = bool(find_claude())
                data["tts"] = kokoro.info()
                return self._json(200, data)
            if self.path == "/api/events":
                return self._events()
            self._json(404, {"error": "not found"})

        def _memory(self, q: dict):
            text = (q.get("q") or [""])[0].strip()
            cat = (q.get("c") or [None])[0] or None
            conn = db.connect()
            try:
                rows = (memory.search(conn, text, category=cat, limit=40) if text
                        else memory.listing(conn, category=cat, limit=200))
            finally:
                conn.close()
            keep = ("id", "title", "category", "product", "provenance", "status", "confidence", "source",
                    "source_ref", "updated", "snippet")
            return self._json(200, [{k: r[k] for k in keep if k in r.keys()} for r in rows])

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q = hub.subscribe()
            conn = db.connect()
            last_sig, last_beat = None, time.time()
            try:
                while True:
                    sig = signature(conn)
                    if sig != last_sig:
                        last_sig = sig
                        self._event({"type": "changed"})
                    try:
                        self._event(q.get(timeout=1.0))
                        while not q.empty():
                            self._event(q.get_nowait())
                    except queue.Empty:
                        pass
                    if time.time() - last_beat > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_beat = time.time()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                hub.unsubscribe(q)
                conn.close()

        def _event(self, data: dict) -> None:
            self.wfile.write(f"data: {json.dumps(data, default=str)}\n\n".encode())
            self.wfile.flush()

        def do_POST(self):
            # Read the body first so a rejected request doesn't leave bytes on a kept-alive connection.
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self._host_ok() or self.headers.get("X-Jarvis-Token") != token:
                return self._json(403, {"error": "forbidden"})
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if self.path == "/api/chat":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "empty message"})
                try:
                    runner.start(text)
                except RuntimeError as exc:
                    return self._json(409, {"error": str(exc)})
                except FileNotFoundError as exc:
                    return self._json(503, {"error": str(exc)})
                return self._json(202, {"ok": True})
            if self.path == "/api/tts":
                try:
                    wav = kokoro.speak(body.get("text") or "", body.get("voice"), body.get("speed"))
                except (tts.TTSError, OSError, ValueError) as exc:
                    return self._json(503, {"error": str(exc)})
                return self._send(200, wav, "audio/wav")
            if self.path == "/api/approval":
                conn = db.connect()
                try:
                    row = records.approval_decide(conn, body.get("id"), bool(body.get("approve")), body.get("note"))
                except (records.RecordError, ValueError, TypeError) as exc:
                    return self._json(400, {"error": str(exc)})
                finally:
                    conn.close()
                return self._json(200, {"ok": True, "status": row["status"]})
            if self.path == "/api/stop":
                runner.stop()
                return self._json(200, {"ok": True})
            if self.path == "/api/new":
                if runner.busy:
                    return self._json(409, {"error": "JARVIS is still answering."})
                runner.reset()
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            import sys
            if not isinstance(sys.exc_info()[1], ConnectionError):  # a closed tab is not an error
                super().handle_error(request, client_address)

    server = Server((host, port), Handler)
    server.hub, server.runner, server.token, server.tts = hub, runner, token, kokoro
    return server


def serve(port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(port=port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"JARVIS dashboard running at {url}  (Ctrl+C to stop)", flush=True)
    if not find_claude():
        print("Note: Claude Code CLI not found, so the page will show live state but can't talk to JARVIS yet.",
              flush=True)
    if server.tts.command():
        threading.Thread(target=server.tts.warm, daemon=True).start()
    else:
        print("Note: Kokoro voice not installed, so JARVIS will use the browser's voice.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.tts.stop()
        server.server_close()
