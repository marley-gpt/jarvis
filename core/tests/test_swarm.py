"""The Live view (T106): agents at work, read from the worker logs and the dashboard session's hand-offs."""

import http.client
import json
import sys
import threading
import time
import unittest.mock

from helpers import CORE, BrainTest

from jarvis import dashboard, records, swarm, work

FAKE = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_claude.py")]


def tool(name, data, ts="2026-11-09T06:00:00.000Z", parent=None, tid="toolu_1"):
    return {"type": "assistant", "parent_tool_use_id": parent, "timestamp": ts,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": data}]}}


class SwarmTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        swarm._logs.clear()
        swarm._workers_cache.clear()
        swarm._session.clear()

    def worker(self, wid, events, status="running", task=""):
        log = work.runs_dir() / f"W{wid}.log"
        log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        (work.runs_dir() / f"W{wid}.json").write_text(json.dumps({
            "id": wid, "task": task, "agent": "creative-director", "brief": "Task T9 (repo x). Make the brush statics",
            "status": status, "pid": 1, "log": str(log), "session": "s", "started": "2026-11-09T16:00:00+10:30"}),
            encoding="utf-8")
        return log

    def test_plain_english_actions(self):
        self.assertEqual(swarm.plain("Edit", {"file_path": "C:/x/core/app.js"}), "Editing app.js")
        self.assertEqual(swarm.plain("Bash", {"command": "ls", "description": "List the runs"}), "Running: List the runs")
        self.assertEqual(swarm.plain("Bash", {"command": 'S="C:/x"; cd /c/repo && python -m unittest -q && ls'}),
                         "Running python -m unittest -q")
        self.assertEqual(swarm.plain("WebSearch", {"query": "lymphatic brush"}), "Searching the web for “lymphatic brush”")
        self.assertEqual(swarm.plain("WebFetch", {"url": "https://www.reddit.com/r/x"}), "Reading a web page on www.reddit.com")
        self.assertEqual(swarm.plain("Agent", {"description": "Mine reviews", "subagent_type": "market-research"}),
                         "Handing “Mine reviews” to market-research")
        self.assertEqual(swarm.plain("mcp__claude_ai_Shopify__get-product", {}), "Using Shopify: get product")

    def test_running_worker_shows_actions_preview_plan_and_helpers(self):
        log = self.worker(5, [
            {"type": "system", "subtype": "init"},
            tool("Read", {"file_path": "brain/work/brief.md"}, "2026-11-09T06:00:01Z"),
            tool("TodoWrite", {"todos": [{"content": "a", "status": "completed"},
                                         {"content": "b", "status": "in_progress", "activeForm": "Writing copy"},
                                         {"content": "c", "status": "pending"}]}, "2026-11-09T06:00:02Z"),
            tool("Write", {"file_path": "brain/work/ads/copy.md", "content": "Headline one\nHeadline two"}, "2026-11-09T06:00:03Z"),
            tool("Agent", {"description": "Mine reviews", "subagent_type": "market-research"}, "2026-11-09T06:00:04Z", tid="toolu_sub1"),
            tool("WebSearch", {"query": "brush reviews"}, "2026-11-09T06:00:05Z", parent="toolu_sub1", tid="toolu_2"),
        ], task="")
        with unittest.mock.patch.object(work, "_alive", return_value=True):
            s = swarm.state(self.conn, {})
        self.assertEqual(s["live"], 2)
        w = next(a for a in s["agents"] if a["key"] == "W5")
        self.assertEqual(w["doing"], "Writing copy")
        self.assertEqual(w["progress"], {"source": "plan", "done": 1, "total": 3, "now": "Writing copy"})
        self.assertEqual(w["made"]["kind"], "text")
        self.assertIn("Headline two", w["made"]["snippet"])
        self.assertEqual(w["title"], "Make the brush statics")
        helper = next(a for a in s["agents"] if a["kind"] == "helper")
        self.assertEqual(helper["name"], "market-research")
        self.assertEqual(helper["actions"], 1)
        texts = [f["text"] for f in s["feed"]]
        self.assertEqual(texts[0], "Reading brief.md")
        self.assertIn("Searching the web for “brush reviews”", texts)
        self.assertEqual(s["feed"][-1]["who"], "market-research")

        # The log grows: only the new part is read, and a half-written line waits for its end.
        line = json.dumps(tool("Edit", {"file_path": "brain/work/ads/copy.md", "new_string": "Headline three"},
                               "2026-11-09T06:00:09Z"))
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line[:30])
        swarm._workers_cache.clear()
        with unittest.mock.patch.object(work, "_alive", return_value=True):
            n = len(swarm.state(self.conn, {})["feed"])
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line[30:] + "\n")
        swarm._workers_cache.clear()
        with unittest.mock.patch.object(work, "_alive", return_value=True):
            s = swarm.state(self.conn, {})
        self.assertEqual(len(s["feed"]), n + 1)
        self.assertEqual(s["feed"][-1]["text"], "Editing copy.md")
        self.assertEqual(next(a for a in s["agents"] if a["key"] == "W5")["made"]["snippet"], "Headline three")

    def test_subtask_progress_when_no_plan(self):
        parent = records.task_add(self.conn, "Launch the brush")
        pid = parent if isinstance(parent, int) else parent["id"]
        for title in ("Research", "Creative"):
            records.task_add(self.conn, title, parent=pid)
        sub = self.conn.execute("SELECT id FROM tasks WHERE parent_id=? ORDER BY id LIMIT 1", (pid,)).fetchone()[0]
        self.conn.execute("UPDATE tasks SET status='done' WHERE id=?", (sub,))
        self.conn.commit()
        self.worker(6, [tool("Read", {"file_path": "x.md"})], task=f"T{pid}")
        with unittest.mock.patch.object(work, "_alive", return_value=True):
            w = swarm.state(self.conn, {})["agents"][0]
        self.assertEqual((w["progress"]["source"], w["progress"]["done"], w["progress"]["total"]), ("subtasks", 1, 2))

    def test_idle_when_nothing_runs(self):
        self.worker(7, [tool("Read", {"file_path": "x.md"}, "2020-01-01T00:00:00Z"),
                        {"type": "result", "result": "Done", "is_error": False}], status="done")
        s = swarm.state(self.conn, {})
        self.assertEqual((s["live"], s["agents"], s["feed"]), (0, [], []))

    def test_session_agents_from_the_dashboard(self):
        swarm.note("shopify", "Took on: PDP draft", "start")
        swarm.note("shopify", "Reading pdp.md")
        s = swarm.state(self.conn, {"agents": {"shopify": {"description": "PDP draft"}}})
        self.assertEqual(s["live"], 1)
        a = s["agents"][0]
        self.assertEqual((a["key"], a["doing"], a["actions"]), ("S:shopify", "Reading pdp.md", 2))
        self.assertEqual([f["text"] for f in s["feed"]], ["Took on: PDP draft", "Reading pdp.md"])

    def test_image_preview_stays_inside_the_repo_and_brain(self):
        img = self.home / "files" / "ad.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"\x89PNG fake")
        self.assertEqual(swarm.image(str(img)), (b"\x89PNG fake", "image/png"))
        (self.home / "secrets").mkdir(exist_ok=True)
        (self.home / "secrets" / "k.png").write_bytes(b"x")
        self.assertIsNone(swarm.image(str(self.home / "secrets" / "k.png")))
        self.assertIsNone(swarm.image(str(self.home / "files" / ".." / ".." / "elsewhere.png")))
        self.assertIsNone(swarm.image(str(self.home / "notes.txt")))


class SwarmRouteTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        swarm._logs.clear()
        swarm._workers_cache.clear()
        swarm._session.clear()
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE}), encoding="utf-8")
        self.server = dashboard.make_server(port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runner.close()
        self.server.tts.stop()
        self.server.server_close()
        super().tearDown()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data
    def live(self):
        return json.loads(self.get("/api/app?fresh=1")[1])["sections"]["live"]

    def test_live_section_of_the_app_state(self):
        data = self.live()
        self.assertEqual((data["count"], data["agents"], data["feed"]), (0, [], []))
        self.assertEqual(data["jarvis"]["status"], "idle")
        self.assertEqual(self.get("/api/swarm/file?path=nothing.png")[0], 204)

    def test_dashboard_hand_offs_reach_the_live_section(self):
        runner = self.server.runner
        dispatched = {}
        runner._handle(tool("Agent", {"description": "Draft the PDP", "subagent_type": "shopify"}, tid="toolu_a"), dispatched)
        runner._handle(tool("Read", {"file_path": "brain/work/pdp.md"}, parent="toolu_a", tid="toolu_b"), dispatched)
        data = self.live()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["agents"][0]["name"], "shopify")
        self.assertEqual([f["text"] for f in data["feed"]][0], "Took on: Draft the PDP")
        runner._handle({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_a", "content": "ok"}]}}, dispatched)
        data = self.live()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["feed"][-1]["text"], "Finished and handed back")


if __name__ == "__main__":
    unittest.main()
