"""End-to-end check of the HQ app against a COPY of the brain, with the real Claude Code and the real connectors.

    python core/tests/e2e_hq.py [--brain <path to the live brain>] [--keep] [--skip-chat]

It never touches the live brain, the store or the ads: the dashboard under test runs on a temporary copy
(memory, journal, tasks, config; secrets left out, so Telegram is off there and nothing reaches the phone;
the voice model is linked, not copied). The desktop job runner and Telegram are checked read-only against the
live brain through the CLI. Chat checks spend a little Claude usage (three short replies). The report is
printed as a table and written to the path given with --report.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
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

LIVE_BRAIN = Path(os.environ.get("JARVIS_LIVE_BRAIN") or r"C:\Users\Desktop\OneDrive\Documents\GitHub\jarvis\brain")
EARLIER_PAGES = ["core/dashboard/chat.html", "core/dashboard/office.html", "core/dashboard/console.html",
                 "core/dashboard/index.html", "core/dashboard/jarvis.js"]
BEFORE_HQ = "5d77c2b^"   # the commit before the HQ work began on this branch
REPLY_TIMEOUT = 300


def copy_brain(live: Path) -> Path:
    home = Path(tempfile.mkdtemp(prefix="jarvis-e2e-"))
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


class Check:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool | None, note: str = "") -> None:
        self.rows.append((name, "PASS" if ok else "SKIP" if ok is None else "FAIL", note))
        print(f"  [{self.rows[-1][1]}] {name}: {note}", flush=True)

    def run(self, name: str, fn) -> None:
        try:
            result = fn()
            if isinstance(result, tuple):
                self.record(name, *result)
            else:
                self.record(name, bool(result), "")
        except Exception as exc:  # noqa: BLE001
            self.record(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")

    def table(self) -> str:
        width = max(len(r[0]) for r in self.rows)
        lines = ["| Check | Result | Note |", "|---|---|---|"]
        lines += [f"| {n} | {s} | {note.replace('|', '/')} |" for n, s, note in self.rows]
        counts = {k: sum(1 for r in self.rows if r[1] == k) for k in ("PASS", "FAIL", "SKIP")}
        lines.append("")
        lines.append(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped.")
        return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default=str(LIVE_BRAIN))
    ap.add_argument("--keep", action="store_true", help="keep the brain copy afterwards")
    ap.add_argument("--skip-chat", action="store_true", help="skip the checks that run Claude Code")
    ap.add_argument("--report", help="write the table here")
    args = ap.parse_args()
    live = Path(args.brain)
    if not (live / "jarvis.db").is_file():
        print(f"No brain at {live}"); return 2

    print(f"Copying the brain from {live} ...")
    home = copy_brain(live)
    os.environ["JARVIS_HOME"] = str(home)
    print(f"Brain copy: {home}")
    from jarvis import brief, db, hq, paths, records
    from jarvis import dashboard

    server = dashboard.make_server(port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    hub_q: queue.Queue = server.hub.subscribe()
    token = server.token
    env = dict(os.environ, JARVIS_HOME=str(home), PYTHONIOENCODING="utf-8")
    live_env = dict(os.environ, JARVIS_HOME=str(live), PYTHONIOENCODING="utf-8")
    cli = [sys.executable, str(CORE / "jarvis_cli.py")]

    def request(method, path, body=None, with_token=True, timeout=60):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
        if with_token:
            headers["X-Jarvis-Token"] = token
        c.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def get_json(path):
        status, raw = request("GET", path)
        return status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)

    def drain():
        while True:
            try:
                hub_q.get_nowait()
            except queue.Empty:
                return

    def wait_reply(timeout=REPLY_TIMEOUT) -> dict:
        """Collect events until JARVIS's reply (and idle); returns {'reply', 'dispatch', 'errors'}."""
        out = {"reply": None, "dispatch": [], "errors": [], "events": 0}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = hub_q.get(timeout=max(1, deadline - time.time()))
            except queue.Empty:
                break
            out["events"] += 1
            t = ev.get("type")
            if t == "reply":
                out["reply"] = ev
            elif t == "dispatch":
                out["dispatch"].append(ev.get("agent"))
            elif t == "error":
                out["errors"].append(ev.get("text"))
            if ev == {"type": "status", "status": "idle"} and (out["reply"] or out["errors"]):
                break
        return out

    def chat(text: str) -> dict:
        drain()
        status, raw = request("POST", "/api/chat", {"text": text})
        if status != 202:
            raise RuntimeError(f"/api/chat {status}: {raw[:200]!r}")
        return wait_reply()

    chk = Check()
    conn = db.connect()
    conn.row_factory = sqlite3.Row
    made_task = {}
    agent_file = hq.AGENTS_DIR / "store-qa.md"
    with open(agent_file, encoding="utf-8", newline="") as f:   # keep its line endings exactly, so the restore is byte-identical
        original_agent = f.read()

    # ---- pages and the earlier designs
    def pages():
        bad = []
        for path in ("/", "/hq", "/chat", "/office", "/console", "/classic"):
            status, html = request("GET", path, with_token=False)
            if status != 200 or token.encode() not in html:
                bad.append(f"{path}:{status}")
        for path in ("/hq.js", "/hq-scene.js", "/jarvis.js"):
            if request("GET", path, with_token=False)[0] != 200:
                bad.append(path)
        return (not bad, "all pages and scripts 200" if not bad else ", ".join(bad))
    chk.run("Pages: / /hq /chat /office /console /classic", pages)

    def unchanged():
        res = subprocess.run(["git", "diff", "--quiet", BEFORE_HQ, "HEAD", "--", *EARLIER_PAGES], cwd=REPO)
        return (res.returncode == 0, "chat, office, console, classic and jarvis.js are byte-identical to before the HQ work"
                if res.returncode == 0 else "differences found")
    chk.run("/chat /office /console unchanged", unchanged)

    def state():
        status, data = get_json("/api/state")
        keys = ("agents", "tasks", "approvals", "memory", "jarvis", "today")
        missing = [k for k in keys if k not in data]
        return (status == 200 and not missing, f"{len(data.get('agents', []))} agents, {data['memory']['total']} memory entries"
                if not missing else f"missing {missing}")
    chk.run("/api/state", state)

    def hq_data():
        status, d = get_json("/api/hq")
        ok = status == 200 and len(d.get("teams", [])) == 8 and len(d.get("agents", [])) == 12 and "models" in d
        return (ok, f"{len(d.get('teams', []))} teams, {len(d.get('agents', []))} agents, models {d.get('models')}")
    chk.run("/api/hq (teams, agents, models)", hq_data)

    def events_stream():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
        c.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        first = r.fp.readline()
        c.close()
        return (r.status == 200 and bool(first), f"status {r.status}, first line {first[:40]!r}")
    chk.run("Live events stream (/api/events)", events_stream)

    # ---- assign a task, model per task, model per agent
    def assign():
        status, raw = request("POST", "/api/hq/task", {"title": "E2E check task: reply with the word READY and close this task",
                                                        "agent": "shopify", "model": "haiku"})
        d = json.loads(raw)
        made_task["id"] = d.get("id")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (made_task["id"],)).fetchone()
        line = brief.task_line(row)
        board = get_json("/api/hq")[1]["board"]   # {"open": [...], "done": [...]}
        on_board = any(t["id"] == made_task["id"] for t in [*board["open"], *board["done"]])
        return (status == 201 and on_board and "(model: haiku" in line,
                f"T{made_task['id']} created for shopify, on the board, brief says '{line.split('(model')[-1][:40]}'")
    chk.run("Assign a task from HQ", assign)

    def task_model():
        tid = f"T{made_task['id']}"
        status, raw = request("POST", "/api/hq/task-model", {"task": tid, "model": "sonnet"})
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (made_task["id"],)).fetchone()
        line = brief.task_line(row)
        saved = (paths.config().get("task_models") or {}).get(tid)
        return (status == 200 and saved == "sonnet" and "(model: sonnet" in line, f"config task_models[{tid}]={saved}; brief carries it")
    chk.run("Model per task saves and reaches the brief", task_model)

    def agent_model():
        status, raw = request("POST", "/api/hq/agent-model", {"agent": "store-qa", "model": "haiku"})
        text = agent_file.read_text(encoding="utf-8")
        m = re.search(r"^model:\s*(\S+)", text, re.M)
        agents = get_json("/api/hq")[1]["agents"]   # a dict keyed by agent name
        shown = (agents.get("store-qa") or {}).get("model") if isinstance(agents, dict) else next(
            (a.get("model") for a in agents if a.get("name") == "store-qa"), None)
        agent_file.write_text(original_agent, encoding="utf-8", newline="")
        with open(agent_file, encoding="utf-8", newline="") as f:
            restored = f.read() == original_agent
        return (status == 200 and m and m.group(1) == "haiku" and shown == "haiku" and restored,
                f"agent file model: {m.group(1) if m else None}, /api/hq shows {shown}, file restored={restored}")
    chk.run("Model per agent saves to the agent file and shows", agent_model)

    # ---- metrics, activity log, skills board
    def metrics():
        snap = {"period": "today", "source": "e2e", "shopify": {"sessions": 123, "orders": 4, "revenue": 256.5},
                "meta": {"spend": 40, "purchases": 2, "roas": 3.2}}
        res = subprocess.run([*cli, "metrics", "set"], input=json.dumps(snap), capture_output=True, text=True, env=env, cwd=REPO)
        d = get_json("/api/hq")[1].get("metrics") or {}
        ok = res.returncode == 0 and d.get("pulled") and d.get("shopify", {}).get("orders") == 4 and d.get("meta", {}).get("roas") == 3.2
        return (bool(ok), f"cli: {res.stdout.strip()[:60] or res.stderr.strip()[:80]}; page shows orders={d.get('shopify', {}).get('orders')} roas={d.get('meta', {}).get('roas')}")
    chk.run("Metrics snapshot shows", metrics)

    def activity_feed():
        res = subprocess.run([*cli, "event", "add", "shopify", "E2E check: drafting the brush page", "--status", "working",
                              "--summary", "e2e"], capture_output=True, text=True, env=env, cwd=REPO)
        status, rows = get_json("/api/activity")
        mine = [r for r in (rows if isinstance(rows, list) else rows.get("events", [])) if "E2E check" in (r.get("action") or "")]
        live = get_json("/api/hq")[1].get("activity") or {}
        in_events = any("E2E check" in (e.get("action") or "") for e in live.get("events", []))
        current = (live.get("agents") or {}).get("shopify")
        return (res.returncode == 0 and status == 200 and bool(mine) and in_events and bool(current),
                f"cli: {res.stdout.strip()[:40]}; /api/activity has it={bool(mine)}; /api/hq events has it={in_events}; "
                f"shopify current={json.dumps(current)[:60] if current else None}")
    chk.run("Activity log feed (/api/activity -> HQ)", activity_feed)

    def skills_board():
        status, d = get_json("/api/skills")
        s2, one = get_json("/api/skills?agent=shopify")
        s3, team = get_json("/api/team")
        n = len(d.get("agents", d) if isinstance(d, dict) else d)
        return (status == 200 and s2 == 200 and s3 == 200, f"/api/skills {status} ({n} agents), per-agent {s2}, /api/team {s3}")
    chk.run("Skills board (/api/skills, /api/team)", skills_board)

    # ---- connectors
    def connectors():
        request("POST", "/api/hq/connectors", {})
        deadline = time.time() + 180
        d = {}
        while time.time() < deadline:
            d = get_json("/api/hq")[1].get("connectors") or {}
            if d.get("checked") and not d.get("running"):
                break
            time.sleep(3)
        servers = d.get("servers") or []
        bad = [s["name"] for s in servers if not s["ok"]]
        detail = "; ".join(f"{s['name']}: {s['status']}" for s in servers)
        return (bool(servers) and not bad and not d.get("error"), detail or f"error: {d.get('error')}")
    chk.run("Every MCP connector connected", connectors)

    # ---- voice
    def tts_check():
        status, wav = request("POST", "/api/tts", {"text": "The estate is ready, sir."}, timeout=180)
        if status == 503:
            return (False, f"503 {wav[:120]!r}")
        return (status == 200 and wav[:4] == b"RIFF" and len(wav) > 2000, f"status {status}, {len(wav)} bytes of WAV")
    chk.run("Voice (Kokoro TTS via /api/tts)", tts_check)

    def wake_info():
        status, d = get_json("/api/state")
        s2, raw = request("POST", "/api/wake", {})
        info = json.loads(raw)
        return (s2 == 200, f"wake word enabled={info.get('enabled')} running={info.get('running')}")
    chk.run("Wake word / mic status (/api/wake)", wake_info)

    # ---- the desktop job runner and Telegram, read-only against the live brain
    def job_runner():
        res = subprocess.run([*cli, "job", "list"], capture_output=True, text=True, env=live_env, cwd=REPO)
        first = (res.stdout.strip().splitlines() or [""])[0]
        return ("running" in first.lower(), first)
    chk.run("Desktop job runner (bin/jarvis job list, live brain)", job_runner)

    def telegram_status():
        res = subprocess.run([*cli, "telegram", "status"], capture_output=True, text=True, env=live_env, cwd=REPO)
        text = res.stdout.strip().replace("\n", "; ")
        return ("Token: set" in text and "paired: yes" in text, text[:120])
    chk.run("Telegram (bin/jarvis telegram status, live brain)", telegram_status)

    def telegram_copy_off():
        info = server.telegram.info()
        return (not info["configured"], f"test copy has no secrets: configured={info['configured']} (nothing reaches the phone)")
    chk.run("Test copy keeps Telegram off", telegram_copy_off)

    # ---- chat to JARVIS and to a team, start a task, approve and reject
    if args.skip_chat:
        for name in ("Chat to JARVIS (reply received)", "Chat to a named team (reply received)", "Start a task from HQ",
                     "Approve from HQ (JARVIS carries it out)", "Reject from HQ"):
            chk.record(name, None, "skipped (--skip-chat)")
    else:
        def chat_jarvis():
            r = chat("E2E check from the dashboard: reply with the single word READY and nothing else.")
            text = (r["reply"] or {}).get("text", "")
            return (bool(text) and not (r["reply"] or {}).get("is_error"), f"reply: {text[:80]!r}; errors={r['errors'][:1]}")
        chk.run("Chat to JARVIS (reply received)", chat_jarvis)

        def chat_team():
            r = chat("Shopify, E2E check: in one short line, say which team you are. Do nothing else.")
            text = (r["reply"] or {}).get("text", "")
            return (bool(text) and not (r["reply"] or {}).get("is_error"),
                    f"reply: {text[:80]!r}; delegated to {r['dispatch'] or 'nobody (JARVIS answered)'}")
        chk.run("Chat to a named team (reply received)", chat_team)

        def start_task():
            tid = made_task.get("id")
            r = chat(f"Shopify, start on T{tid} now. It only needs the word READY; then close the task with task done.")
            text = (r["reply"] or {}).get("text", "")
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
            return (bool(text) and not (r["reply"] or {}).get("is_error"),
                    f"reply: {text[:60]!r}; T{tid} is now '{row['status'] if row else '?'}'; delegated to {r['dispatch'] or 'nobody'}")
        chk.run("Start a task from HQ", start_task)

        def approve():
            aid = records.approval_request(conn, "other", "E2E check approval: nothing to carry out, just reply DONE")
            drain()
            status, raw = request("POST", "/api/approval", {"id": f"A{aid}", "approve": True})
            d = json.loads(raw)
            r = wait_reply()
            text = (r["reply"] or {}).get("text", "")
            row = conn.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()
            return (status == 200 and d.get("status") == "approved" and bool(text),
                    f"A{aid} {row['status']}; JARVIS carried it out and replied {text[:60]!r}")
        chk.run("Approve from HQ (JARVIS carries it out)", approve)

        def reject():
            aid = records.approval_request(conn, "spend", "E2E check approval to reject", amount=1, currency="AUD")
            status, raw = request("POST", "/api/approval", {"id": f"A{aid}", "approve": False, "note": "e2e"})
            d = json.loads(raw)
            row = conn.execute("SELECT status FROM approvals WHERE id = ?", (aid,)).fetchone()
            return (status == 200 and d.get("status") == "rejected" and row["status"] == "rejected", f"A{aid} {row['status']}, no run started")
        chk.run("Reject from HQ", reject)

    # ---- wrap up
    conn.close()
    server.shutdown()
    server.runner.close()
    server.tts.stop()
    server.server_close()
    table = chk.table()
    print("\n" + table)
    if args.report:
        Path(args.report).write_text(f"# HQ end-to-end check\n\nBrain copy of {live}, dashboard on the merged branch, "
                                     f"{time.strftime('%Y-%m-%d %H:%M')}.\n\n{table}\n", encoding="utf-8")
    if args.keep:
        print(f"Brain copy kept at {home}")
    else:
        remove_copy(home)
    return 0 if all(r[1] != "FAIL" for r in chk.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
