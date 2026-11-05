"""Measure how fast a spoken conversation is: time to the first spoken sentence and to the whole reply.

    python core/tests/latency_hq.py [--brain <live brain>] [--model sonnet] [--turns 3]

Runs the dashboard on a COPY of the brain (like e2e_hq.py) with the real Claude Code, sends short messages and
records, per turn: the time from POST /api/chat to "thinking", to the first streamed sentence (say_chunk: when
the voice can start), to the full reply, and to idle. Also times the Kokoro voice (first sentence, cold and
warm) and the per-message prompt hook. Prints a table; --report writes it as Markdown.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(CORE / "tests"))
from e2e_hq import LIVE_BRAIN, copy_brain, remove_copy  # noqa: E402

MESSAGES = ["Say good evening in one short sentence.", "What day is it today? One sentence.", "Thank you. One short line back."]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default=str(LIVE_BRAIN))
    ap.add_argument("--model", help="dashboard_model to set in the copy (sonnet, haiku, opus); default: as configured")
    ap.add_argument("--effort", help="dashboard_effort to set in the copy (low, medium, high)")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--report")
    ap.add_argument("--label", default="")
    ap.add_argument("--prewarm", type=float, default=0, help="start Claude Code at server start and wait this many seconds before turn 1")
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
    from jarvis import dashboard, paths

    server = dashboard.make_server(port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    q: queue.Queue = server.hub.subscribe()
    token = server.token
    rows = []

    def post(path, body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
        c.request("POST", path, json.dumps(body), {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-Jarvis-Token": token})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data

    # The voice: first sentence cold (worker start + model load) and warm.
    for label in ("voice: first sentence, cold", "voice: first sentence, warm"):
        t = time.perf_counter()
        status, wav = post("/api/tts", {"text": "Good evening, sir. The estate is ready."})
        rows.append((label, round(time.perf_counter() - t, 2), f"{status}, {len(wav)} bytes"))

    # The per-message hook (memory recall + journal), on this brain's size.
    event = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "latency", "prompt": MESSAGES[0]})
    for i in range(2):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-c", "import sys; sys.argv=['hook','prompt']; from jarvis import hooks; hooks.main(['prompt'])"],
                       input=event, capture_output=True, text=True, cwd=CORE, env=dict(os.environ, JARVIS_HOME=str(home)))
        rows.append((f"prompt hook (recall + journal), run {i + 1}", round(time.perf_counter() - t, 2), f"{home.name}"))

    def drain():
        while True:
            try: q.get_nowait()
            except queue.Empty: return

    if args.prewarm:   # what bin/jarvis dashboard now does at start: Claude Code already running before the first word
        t = time.perf_counter()
        server.runner.warm()
        for _ in range(300):
            if server.runner.proc and server.runner.proc.poll() is None: break
            time.sleep(0.1)
        time.sleep(args.prewarm)
        rows.append(("Claude Code pre-started at server start, then waited", round(time.perf_counter() - t, 2), "before turn 1"))

    for i, text in enumerate(MESSAGES[:args.turns]):
        drain()
        t0 = time.perf_counter()
        status, raw = post("/api/chat", {"text": text})
        marks = {"thinking": None, "first_sentence": None, "reply": None, "idle": None}
        first_text = ""
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                ev = q.get(timeout=max(1, deadline - time.time()))
            except queue.Empty:
                break
            now = time.perf_counter() - t0
            if ev.get("type") == "status" and ev.get("status") == "thinking" and marks["thinking"] is None:
                marks["thinking"] = now
            elif ev.get("type") == "say_chunk" and marks["first_sentence"] is None:
                marks["first_sentence"] = now; first_text = ev.get("text", "")
            elif ev.get("type") == "reply" and marks["reply"] is None:
                marks["reply"] = now
                if marks["first_sentence"] is None: first_text = ev.get("text", "")
            elif ev == {"type": "status", "status": "idle"}:
                marks["idle"] = now; break
        kind = "cold (process start)" if i == 0 else "warm"
        rows.append((f"turn {i + 1} ({kind}): first sentence", marks["first_sentence"] if marks["first_sentence"] is not None else marks["reply"], first_text[:50]))
        rows.append((f"turn {i + 1} ({kind}): full reply", marks["reply"], f"idle at {marks['idle']}"))
    server.shutdown(); server.runner.close(); server.tts.stop(); server.server_close()
    remove_copy(home)
    model = (cfg.get("dashboard_model") or "(JARVIS's default)") + (f", effort {cfg['dashboard_effort']}" if cfg.get("dashboard_effort") else "")
    lines = [f"| Step | Seconds | Note |", "|---|---|---|"] + [f"| {n} | {('%.2f' % v) if isinstance(v, (int, float)) else '-'} | {note} |" for n, v, note in rows]
    text = f"Conversation model: {model}. {args.label}\n\n" + "\n".join(lines)
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
