"""Measure how fast the app is: the state the pages draw from, and a spoken conversation.

    python core/tests/latency.py [--brain <live brain>] [--model sonnet] [--turns 3] [--skip-chat]

Runs the dashboard on a COPY of the brain (never the live one) with the real Claude Code and records:
- the app's state: the first build, a cached read, a rebuild, and how big the first message of the stream is;
- the Kokoro voice: first sentence, cold and warm; the per-message prompt hook;
- per turn: the time from POST /api/chat to the first written words (say_delta: when the page shows them), to the
  first spoken sentence (say_chunk: when the voice can start), to the full reply, and to idle.
Prints a table; --report writes it as Markdown. `--skip-chat` leaves out the turns, which spend Claude usage.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
sys.path.insert(0, str(CORE))

LIVE_BRAIN = Path(os.environ.get("JARVIS_LIVE_BRAIN") or REPO / "brain")
MESSAGES = ["Say good evening in one short sentence.", "What day is it today? One sentence.", "Thank you. One short line back."]


def copy_brain(live: Path) -> Path:
    """A throwaway copy of the brain: records, memory and work, with the voice linked rather than copied."""
    home = Path(tempfile.mkdtemp(prefix="jarvis-latency-"))
    for name in ("config.json", "readiness.json"):
        if (live / name).is_file():
            shutil.copy2(live / name, home / name)
    for folder in ("memory", "journal", "feedback", "skills", "reports", "work"):
        if (live / folder).is_dir():
            shutil.copytree(live / folder, home / folder)
    for folder in ("files", "backups", "scratch", "secrets", "telegram"):
        (home / folder).mkdir(exist_ok=True)
    src = sqlite3.connect(str(live / "jarvis.db"))
    dst = sqlite3.connect(str(home / "jarvis.db"))
    with dst:
        src.backup(dst)
        dst.execute("DELETE FROM meta WHERE key IN ('dashboard_session', 'claude_login_needed')")
    src.close(); dst.close()
    if (live / "tts").is_dir():   # the voice venv and model, 850 MB: link, don't copy
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(home / "tts"), str(live / "tts")], capture_output=True)
        else:
            os.symlink(live / "tts", home / "tts")
    return home


def remove_copy(home: Path) -> None:
    link = home / "tts"
    if link.exists():
        try:
            os.rmdir(link)   # removes the junction, not what it points to
        except OSError:
            pass
    shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default=str(LIVE_BRAIN))
    ap.add_argument("--model", help="dashboard_model to set in the copy (sonnet, haiku, opus); default: as configured")
    ap.add_argument("--effort", help="dashboard_effort to set in the copy (low, medium, high)")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--skip-chat", action="store_true")
    ap.add_argument("--no-prime", action="store_true", help="start Claude Code without the warm-up exchange")
    ap.add_argument("--report")
    ap.add_argument("--label", default="")
    ap.add_argument("--prewarm", type=float, default=3, help="seconds between Claude Code being ready and turn 1")
    args = ap.parse_args()
    home = copy_brain(Path(args.brain))
    os.environ["JARVIS_HOME"] = str(home)
    cfg_path = home / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    if args.model:
        cfg["dashboard_model"] = args.model
    if args.effort:
        cfg["dashboard_effort"] = args.effort
    cfg["wake_word"] = False
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    from jarvis import dashboard

    server = dashboard.make_server(port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    q: queue.Queue = server.hub.subscribe()
    token = server.token
    rows = []

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-Jarvis-Token": token})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data

    def timed(label, fn, note=lambda out: ""):
        t = time.perf_counter()
        out = fn()
        rows.append((label, round(time.perf_counter() - t, 3), note(out)))
        return out

    # The state every page draws from.
    size = lambda out: f"{out[0]}, {len(out[1]) // 1024} KB"  # noqa: E731
    timed("app state: first build (memory index included)", lambda: call("GET", "/api/app"), size)
    timed("app state: read again within a second (cached)", lambda: call("GET", "/api/app"), size)
    timed("app state: forced rebuild", lambda: call("GET", "/api/app?fresh=1"), size)
    timed("team map (fetched when the Team page opens)", lambda: call("GET", "/api/teammap"), size)
    timed("memory search", lambda: call("GET", "/api/memory?q=breakeven"), size)

    # The voice: first sentence cold (worker start + model load) and warm.
    for label in ("voice: first sentence, cold", "voice: first sentence, warm"):
        timed(label, lambda: call("POST", "/api/tts", {"text": "Good evening, sir. The pipeline is ready."}),
              lambda out: f"{out[0]}, {len(out[1])} bytes")

    # The per-message hook (memory recall + journal), on this brain's size.
    event = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "latency", "prompt": MESSAGES[0]})
    for i in range(2):
        timed(f"prompt hook (recall + journal), run {i + 1}", lambda: subprocess.run(
            [sys.executable, "-c", "import sys; sys.argv=['hook','prompt']; from jarvis import hooks; hooks.main(['prompt'])"],
            input=event, capture_output=True, text=True, cwd=CORE, env=dict(os.environ, JARVIS_HOME=str(home))))

    from jarvis import db, memory
    conn = db.connect()
    timed("index the copied brain (one-off: the copy's paths are new)", lambda: memory.sync(conn))
    conn.close()

    def drain():
        while True:
            try: q.get_nowait()
            except queue.Empty: return

    if not args.skip_chat:
        t = time.perf_counter()
        server.runner.prime = not args.no_prime
        server.runner.warm()   # what bin/jarvis dashboard does at start, and again whenever Claude Code has ended
        for _ in range(300):
            if server.runner.proc and server.runner.proc.poll() is None: break
            time.sleep(0.1)
        time.sleep(1)
        primed = server.runner.primed.wait(120)
        rows.append(("Claude Code started" + (" and primed" if server.runner.prime else ""), round(time.perf_counter() - t, 2), f"primed={primed}"))
        time.sleep(args.prewarm)
        for i, text in enumerate(MESSAGES[:args.turns]):
            drain()
            t0 = time.perf_counter()
            call("POST", "/api/chat", {"text": text})
            marks = {"words": None, "first_sentence": None, "reply": None, "idle": None}
            first_text = ""
            deadline = time.time() + 240
            while time.time() < deadline:
                try:
                    ev = q.get(timeout=max(1, deadline - time.time()))
                except queue.Empty:
                    break
                now = time.perf_counter() - t0
                if ev.get("type") == "say_delta" and marks["words"] is None:
                    marks["words"] = now
                elif ev.get("type") == "say_chunk" and marks["first_sentence"] is None:
                    marks["first_sentence"] = now; first_text = ev.get("text", "")
                elif ev.get("type") == "reply" and marks["reply"] is None:
                    marks["reply"] = now
                    if marks["first_sentence"] is None: first_text = ev.get("text", "")
                elif ev == {"type": "status", "status": "idle"}:
                    marks["idle"] = now; break
            kind = "first after start" if i == 0 else "warm"
            rows.append((f"turn {i + 1} ({kind}): first words on the page", marks["words"], ""))
            rows.append((f"turn {i + 1} ({kind}): first spoken sentence", marks["first_sentence"] if marks["first_sentence"] is not None else marks["reply"], first_text[:50]))
            rows.append((f"turn {i + 1} ({kind}): full reply", marks["reply"], f"idle at {marks['idle'] and round(marks['idle'], 2)}"))
    server.shutdown(); server.runner.close(); server.tts.stop(); server.server_close()
    remove_copy(home)
    model = (cfg.get("dashboard_model") or "(the default)") + (f", effort {cfg['dashboard_effort']}" if cfg.get("dashboard_effort") else "")
    lines = ["| Step | Seconds | Note |", "|---|---|---|"] + [f"| {n} | {('%.2f' % v) if isinstance(v, (int, float)) else '-'} | {note} |" for n, v, note in rows]
    text = f"Conversation model: {model}. {args.label}\n\n" + "\n".join(lines)
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
