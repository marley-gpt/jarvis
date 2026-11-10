import base64
import http.client
import json
import sys
import threading
import time
import unittest.mock

from helpers import CORE, BrainTest

from jarvis import dashboard, db, records, wakeword

FAKE = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_claude.py")]
FAKE_TTS = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_tts.py")]


class DashboardTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE}), encoding="utf-8")
        self.server = dashboard.make_server(port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.events = []
        self.hub_q = self.server.hub.subscribe()

    def tearDown(self):
        self.server.shutdown()
        self.server.runner.close()
        self.server.tts.stop()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-Jarvis-Token"] = self.server.token
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def wait_idle(self):
        deadline = time.time() + 15
        while time.time() < deadline:
            ev = self.hub_q.get(timeout=15)
            self.events.append(ev)
            if ev == {"type": "status", "status": "idle"}:
                return
        self.fail("runner never finished")

    def test_every_page_carries_the_live_script_and_reports_its_version(self):
        from jarvis import live
        for page in ("/", "/chat", "/console"):
            status, html = self.request("GET", page, token=False)
            self.assertEqual(status, 200)
            self.assertIn(b'<script src="/live.js"></script>', html)
        status, js = self.request("GET", "/live.js", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"/api/live", js)
        status, raw = self.request("GET", "/api/live", token=False)
        self.assertEqual(json.loads(raw), {"ui": live.ui_version(), "boot": live.BOOT})

    def test_page_and_state(self):
        status, html = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), html)
        records.task_add(self.conn, "Research posture corrector", agent="product-research")
        status, raw = self.request("GET", "/api/state", token=False)
        state = json.loads(raw)
        names = [a["name"] for a in state["agents"]]
        self.assertEqual(names[0], "mentor-learning")
        self.assertEqual(len(names), 12)
        self.assertIn("store-qa", names)
        self.assertIn("accountant", names)
        pr = next(a for a in state["agents"] if a["name"] == "product-research")
        self.assertEqual(pr["status"], "queued")
        self.assertEqual(pr["now"], "Research posture corrector")
        self.assertTrue(state["jarvis"]["claude"])

    def test_prototype_pages_and_shared_client(self):
        for path in ("/console", "/office", "/chat", "/classic"):
            status, html = self.request("GET", path, token=False)
            self.assertEqual(status, 200, path)
            self.assertIn(self.server.token.encode(), html)
        status, js = self.request("GET", "/jarvis.js", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"window.J = J", js)
        self.assertEqual(self.request("GET", "/nope", token=False)[0], 404)

    def test_state_has_console_sections(self):
        records.product_add(self.conn, "Posture Corrector")
        parent = records.task_add(self.conn, "Launch posture corrector")
        records.task_add(self.conn, "Research it", agent="product-research", parent=parent)
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertEqual(state["counts"]["delegations"], 1)
        self.assertEqual(state["counts"]["open_tasks"], 2)
        self.assertEqual([p["slug"] for p in state["products"]], ["posture-corrector"])
        self.assertIn("by_service", state["cost"])
        pr = next(a for a in state["agents"] if a["name"] == "product-research")
        self.assertEqual((pr["open"], pr["runs"]), (1, 0))

    def test_memory_search_and_entry(self):
        rows = json.loads(self.request("GET", "/api/memory?q=approval", token=False)[1])
        self.assertTrue(rows)
        self.assertIn("snippet", rows[0])
        everything = json.loads(self.request("GET", "/api/memory", token=False)[1])
        self.assertGreaterEqual(len(everything), len(rows))
        status, raw = self.request("GET", f"/api/memory/{rows[0]['id']}", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["meta"]["id"], rows[0]["id"])
        self.assertEqual(self.request("GET", "/api/memory/M-NOPE", token=False)[0], 404)

    def test_brain_page_graph_nodes_and_live_panel(self):
        from jarvis import brain_graph, memory
        status, html = self.request("GET", "/brain", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b'<script src="/brain.js"></script>', html)
        self.assertIn(b'id="orb"', html)
        status, js = self.request("GET", "/brain.js", token=False)
        self.assertEqual(status, 200)
        for needle in (b"/api/brain/graph", b"/api/brain/now", b"/api/brain/node"):
            self.assertIn(needle, js)

        slug = records.product_add(self.conn, "Posture Corrector")
        parent = records.task_add(self.conn, "Launch posture corrector", product=slug)
        child = records.task_add(self.conn, "Research it", agent="product-research", parent=parent)
        records.task_update(self.conn, child, status="in_progress")
        dec = records.decision_add(self.conn, "Test it at A$30/day", task=parent)
        stray = records.task_add(self.conn, "Build a page", agent="general-purpose")  # an agent outside the roster
        records.task_add(self.conn, "Check it", agent="general-purpose", parent=stray)
        old, _ = memory.remember(self.conn, title="Posture angle v1", body="Back pain angle.", category="testing",
                                 provenance="user", product=slug)
        new, _ = memory.remember(self.conn, title="Posture angle v2", category="testing", provenance="user",
                                 supersedes=old.meta["id"],
                                 body=f"Desk workers angle, from T{parent} and D{dec}; see [[Posture angle v1]].")
        old_id, new_id = old.meta["id"], new.meta["id"]

        g = json.loads(self.request("GET", "/api/brain/graph", token=False)[1])
        ids = [n["id"] for n in g["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        for nid in ("jarvis", "agent:product-research", f"product:{slug}", f"T{parent}", f"T{child}", f"D{dec}",
                    "cat:testing", old_id, new_id):
            self.assertIn(nid, ids)
        edges = {(ids[a], ids[b], kind) for a, b, kind in g["links"]}
        undirected = {frozenset((a, b)) for a, b, _ in edges}
        for a, b in ((new_id, old_id), (f"T{child}", f"T{parent}"), (f"T{child}", "agent:product-research"),
                     (f"T{parent}", f"product:{slug}"), (f"D{dec}", f"T{parent}"), (old_id, f"product:{slug}"),
                     (new_id, f"D{dec}"), (new_id, "cat:testing"), ("jarvis", "agent:product-research")):
            self.assertIn(frozenset((a, b)), undirected, (a, b))
        self.assertIn((new_id, old_id, "supersedes"), edges)
        self.assertTrue(all(0 <= a < len(ids) and 0 <= b < len(ids) and a != b for a, b, _ in g["links"]))
        self.assertEqual(g["counts"]["task"], 4)
        reach, todo = {0}, [0]  # every node hangs off JARVIS, so nothing drifts off the layout
        near = {}
        for a, b, _ in g["links"]:
            near.setdefault(a, []).append(b)
            near.setdefault(b, []).append(a)
        while todo:
            for j in near.get(todo.pop(), []):
                if j not in reach:
                    reach.add(j)
                    todo.append(j)
        self.assertEqual(len(reach), len(ids))
        status, raw = self.request("GET", "/api/brain/graph", token=False)
        self.assertEqual(json.loads(raw)["sig"], g["sig"])  # cached until the brain changes

        node = json.loads(self.request("GET", f"/api/brain/node?id={new_id}", token=False)[1])
        self.assertEqual((node["kind"], node["meta"]["supersedes"]), ("memory", old_id))
        task = json.loads(self.request("GET", f"/api/brain/node?id=T{parent}", token=False)[1])
        self.assertEqual([s["id"] for s in task["subtasks"]], [child])
        agent = json.loads(self.request("GET", "/api/brain/node?id=agent:product-research", token=False)[1])
        self.assertEqual([t["id"] for t in agent["tasks"]], [child])
        self.assertEqual(json.loads(self.request("GET", f"/api/brain/node?id=product:{slug}", token=False)[1])["memory"], 1)
        self.assertEqual(self.request("GET", "/api/brain/node?id=T99999", token=False)[0], 404)
        self.assertEqual(self.request("GET", "/api/brain/node?id=agent:nobody", token=False)[0], 404)
        self.assertEqual(self.request("GET", "/api/brain/nope", token=False)[0], 404)

        now = json.loads(self.request("GET", "/api/brain/now", token=False)[1])
        self.assertEqual(set(now) >= {"busy", "workers", "agents", "tasks", "approvals", "journal", "sig"}, True)
        self.assertEqual(now["tasks"][0]["id"], child)  # work in progress first
        self.assertEqual(now["sig"], g["sig"])
        records.task_add(self.conn, "Another job")
        self.assertNotEqual(json.loads(self.request("GET", "/api/brain/now", token=False)[1])["sig"], g["sig"])

        # A running worker makes the orb pulse.
        from jarvis import work
        (work.runs_dir() / "W7.json").write_text(json.dumps({"id": 7, "task": f"T{child}", "agent": "product-research",
            "brief": "Research it", "status": "running", "pid": None, "log": str(self.home / "W7.log"),
            "session": "x", "started": "2026-11-09T01:00:00"}), encoding="utf-8")
        brain_graph._cache.pop("workers", None)
        with unittest.mock.patch.object(work, "_alive", return_value=True):
            now = json.loads(self.request("GET", "/api/brain/now", token=False)[1])
        self.assertTrue(now["busy"])
        self.assertEqual((now["workers"][0]["id"], now["workers"][0]["title"]), ("W7", "Research it"))

    def test_main_page_combines_stage_and_brain(self):
        status, html = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        for needle in (b'<script src="/jarvis.js"></script>', b'<script src="/stage.js"></script>',
                       b'<script src="/brain.js"></script>', b'<script src="/app.js"></script>',
                       b"window.BRAIN_EMBED = true", b'id="canvas"', b'id="fx"', b'id="orb"', b'id="stage"',
                       b'id="paneNow"', b'id="ticker"', b'id="talk"', b'id="mic"', b'id="demo"',
                       b'href="/stage"', b'href="/brain"', b'href="/chat"', b'href="/hq"'):
            self.assertIn(needle, html)
        self.assertIn(self.server.token.encode(), html)
        self.assertEqual(self.request("GET", "/app", token=False)[1], html)
        # Every id the two scripts look up exists once on the page.
        import re
        ids = re.findall(rb'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        for script in ("stage.js", "brain.js"):
            js = self.request("GET", f"/{script}", token=False)[1]
            for found in set(re.findall(rb'\$\("([A-Za-z0-9]+)"\)', js)) - {b"orbLabel"}:  # /brain's own orb only
                self.assertIn(found, ids, (script, found))
        status, js = self.request("GET", "/app.js", token=False)
        self.assertEqual(status, 200)
        for needle in (b"jarvis:beat", b"jarvis:clear", b"BRAIN_FOCUS", b".light("):
            self.assertIn(needle, js)
        self.assertIn(b"BRAIN_EMBED", self.request("GET", "/brain.js", token=False)[1])
        stage = self.request("GET", "/stage.js", token=False)[1]
        self.assertIn(b"jarvis:beat", stage)
        self.assertIn(b"data-ref", stage)
        # The earlier pages stay where they were.
        self.assertIn(b"/stage.js", self.request("GET", "/stage", token=False)[1])
        self.assertNotIn(b"/app.js", self.request("GET", "/stage", token=False)[1])
        self.assertIn(b"/brain.js", self.request("GET", "/brain", token=False)[1])

    def test_showcase_mode_on_the_main_page(self):
        # T108: /#show and the Show button, drawn from the ledger's own endpoints and the Live view's feed.
        import re
        status, html = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        for needle in (b'id="showBtn"', b'id="show"', b'id="showFx"', b'id="showFig"', b'id="showRail"',
                       b'id="showCrew"', b'id="showFeed"', b'id="showStatus"', b'<script src="/show.js"></script>'):
            self.assertIn(needle, html)
        self.assertLess(html.index(b"/swarm.js"), html.index(b"/show.js"))
        status, js = self.request("GET", "/show.js", token=False)
        self.assertEqual(status, 200)
        for needle in (b'"#show"', b"/api/ops/sales", b"/api/ops/roas", b"/api/swarm", b"n/a", b"No live data",
                       b"prefers-reduced-motion", b"wakeLock", b"requestFullscreen"):
            self.assertIn(needle, js)
        ids = set(re.findall(rb'id="([^"]+)"', html))
        for found in set(re.findall(rb'\$\("([A-Za-z0-9]+)"\)', js)):
            self.assertIn(found, ids, found)
        # The figures it reads are served, with the fields the display names.
        status, raw = self.request("GET", "/api/ops/sales", token=False)
        self.assertEqual(status, 200)
        fig = json.loads(raw)["figures"]
        for key in ("sales_aud", "sales_count", "ad_spend_aud", "profit_today_aud", "profit_week_aud", "weekly_target_aud"):
            self.assertIn(key.encode(), js)
            self.assertIn(key, fig)
        status, raw = self.request("GET", "/api/ops/roas", token=False)
        self.assertEqual(status, 200)
        r = json.loads(raw)
        for key in ("live", "why_not_live", "breakeven", "week", "today"):
            self.assertIn(key, r)
        self.assertIsNone(r["week"]["roas"])  # nothing spent in an empty ledger: the display shows n/a, not 0×
        self.assertEqual(self.request("GET", "/api/swarm", token=False)[0], 200)

    def test_showcase_is_in_the_dashboard_blue(self):
        # M-261110-c6a8: the owner wants the showcase in the dashboard's own blue, not red and amber.
        import re
        html = self.request("GET", "/", token=False)[1].decode()
        start = html.index("Showcase mode (T108)")
        block = html[start:html.index("Team map (T109)", start)]
        for red in ("#ff2d3f", "#ffa62b", "255, 45, 63", "255, 166, 43", "--ember", "--amber"):
            self.assertNotIn(red, block)
        self.assertIn("var(--arc)", block)
        self.assertIn("var(--ice)", block)
        js = self.request("GET", "/show.js", token=False)[1].decode()
        self.assertIn('rgb("--arc"', js)
        self.assertNotIn("255, 45, 63", js)
        self.assertFalse(re.search(r"\[255,\s*\d+,\s*\d+\]", js))

    def test_team_map_shows_every_agent_by_department(self):
        # T109: /#team, the org chart: JARVIS, the twelve specialists in six departments, skills, SOP and live work.
        import re
        from jarvis import orgchart
        status, html = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        for needle in (b'id="teamBtn"', b'id="team"', b'id="teamStage"', b'id="teamFx"', b'id="teamPanel"',
                       b'id="teamFlow"', b'href="#show"', b'href="#team"', b'<script src="/team.js"></script>'):
            self.assertIn(needle, html)
        self.assertLess(html.index(b"/show.js"), html.index(b"/team.js"))
        status, js = self.request("GET", "/team.js", token=False)
        self.assertEqual(status, 200)
        for needle in (b'"#team"', b"/api/teammap", b"/api/swarm", b"/api/skills?agent=", b"J.send", b"prefers-reduced-motion"):
            self.assertIn(needle, js)
        ids = set(re.findall(rb'id="([^"]+)"', html))
        for found in set(re.findall(rb'\$\("([A-Za-z0-9]+)"\)', js)) - {b"teamNow", b"teamLevel", b"teamAsk", b"teamQ", b"teamReply"}:
            self.assertIn(found, ids, found)
        # The older /api/team (teams, hires, pace) still answers as before.
        self.assertIn("teams", json.loads(self.request("GET", "/api/team", token=False)[1]))
        # The map itself, from the agent files and the task records.
        tid = records.task_add(self.conn, "Brush launch")
        a = records.task_add(self.conn, "Research the brush", parent=tid, agent="market-research")
        records.task_update(self.conn, a, status="done", summary="done")
        b = records.task_add(self.conn, "Statics for the brush", parent=tid, agent="creative-director")
        status, raw = self.request("GET", "/api/teammap", token=False)
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual([d["id"] for d in data["departments"]][:6], ["research", "creative", "store", "ads", "money", "ops"])
        placed = [n for d in data["departments"] for n in d["agents"]]
        on_disk = sorted(p.stem for p in orgchart.AGENTS_DIR.glob("*.md"))
        self.assertEqual(sorted(placed), on_disk)
        self.assertEqual(len(placed), len(set(placed)))
        shop = data["agents"]["shopify"]
        self.assertIn("blake-shopify-cro", shop["skills"])
        self.assertIn("cro", shop["playbooks"])
        self.assertTrue(shop["sop"] and shop["sop"][0]["title"] == "Role")
        self.assertNotIn("Skills", [s["title"] for s in shop["sop"]])
        cd = data["agents"]["creative-director"]
        self.assertEqual([t["id"] for t in cd["open"]], [f"T{b}"])
        hand = data["handoffs"][-1]
        self.assertEqual((hand["from"], hand["to"], hand["task"]), ("market-research", "creative-director", f"T{b}"))
        self.assertIn("total", data["memory"])
        self.assertIn("open_projects", data["jarvis"])

    def test_records_a_reply_names_are_nodes_in_the_graph(self):
        from jarvis import brain_graph
        slug = records.product_add(self.conn, "Posture Corrector")
        tid = records.task_add(self.conn, "Draft the page", agent="shopify", product=slug)
        records.task_update(self.conn, tid, deliverable="brain/work/shopify/page.md")
        aid = records.approval_request(self.conn, "spend", "Spend A$30 on the test", task=tid, amount=30, currency="AUD")
        lone = records.approval_request(self.conn, "other", "Something with no task")
        dec = records.decision_add(self.conn, "Test it", task=tid)
        from jarvis import memory
        mem, _ = memory.remember(self.conn, title="Page notes", body="Notes.", category="shopify", provenance="user",
                                 source_ref="brain/work/shopify/page.md")
        mid = mem.meta["id"]
        g = json.loads(self.request("GET", "/api/brain/graph", token=False)[1])
        ids = [n["id"] for n in g["nodes"]]
        undirected = {frozenset((ids[a], ids[b])) for a, b, _ in g["links"]}
        fid = "file:brain/work/shopify/page.md"
        for a, b in ((f"A{aid}", f"T{tid}"), (fid, f"T{tid}"), (fid, mid)):
            self.assertIn(frozenset((a, b)), undirected, (a, b))
        self.assertIn(f"A{lone}", ids)
        self.assertEqual(ids.count(fid), 1)
        self.assertEqual(g["counts"]["approval"], 2)

        # Every record card the speaking visuals make for a hint is a node the main page can light.
        text = f"[[show T{tid} A{aid} D{dec} {mid} {slug}]] [[file brain/work/shopify/page.md]]"
        cards = json.loads(self.request("POST", "/api/visuals", {"text": text})[1])["cards"]
        ref = {"task": lambda c: c["id"], "approval": lambda c: c["id"], "decision": lambda c: c["id"],
               "memory": lambda c: c["id"], "product": lambda c: f"product:{c['id']}", "file": lambda c: f"file:{c['path']}"}
        self.assertEqual(len(cards), 4)  # never more than four
        for c in cards:
            self.assertIn(ref[c["kind"]](c), ids, c)
        file_cards = json.loads(self.request("POST", "/api/visuals", {"text": "[[file brain/work/shopify/page.md]]"})[1])["cards"]
        self.assertIn(ref["file"](file_cards[0]), ids)

        a = json.loads(self.request("GET", f"/api/brain/node?id=A{aid}", token=False)[1])
        self.assertEqual((a["kind"], a["row"]["task_id"], a["row"]["status"]), ("approval", tid, "pending"))
        f = json.loads(self.request("GET", f"/api/brain/node?id={fid}", token=False)[1])
        self.assertEqual(([t["id"] for t in f["tasks"]], [m["id"] for m in f["recent"]]), ([tid], [mid]))
        self.assertEqual(self.request("GET", "/api/brain/node?id=file:brain/nothing.md", token=False)[0], 404)
        self.assertEqual(self.request("GET", "/api/brain/node?id=A99999", token=False)[0], 404)

        # Deciding an approval changes the fingerprint, so the page refetches and the node's status follows.
        records.approval_decide(self.conn, aid, True)
        self.assertNotEqual(json.loads(self.request("GET", "/api/brain/now", token=False)[1])["sig"], g["sig"])
        g2 = json.loads(self.request("GET", "/api/brain/graph", token=False)[1])
        self.assertEqual(next(n for n in g2["nodes"] if n["id"] == f"A{aid}")["s"], "approved")
        brain_graph._cache.clear()

    def test_brain_graph_is_fast_with_1200_entries(self):
        from jarvis import brain_graph
        rows, fts = [], []
        for i in range(1300):
            mid = f"M-261109-{i:04x}"
            rows.append((mid, f"memory/procedures/e{i}.md", f"Entry {i}", ("procedures", "mentor", "shopify")[i % 3],
                         "user", "active"))
            fts.append((mid, f"Entry {i}", "", f"Linked to M-261109-{(i * 7) % 1300:04x} and T1."))
        self.conn.executemany("INSERT INTO memory (id, path, title, category, provenance, status) VALUES (?,?,?,?,?,?)", rows)
        self.conn.executemany("INSERT INTO memory_fts (id, title, tags, body) VALUES (?,?,?,?)", fts)
        records.task_add(self.conn, "One task")
        self.conn.commit()
        brain_graph._cache.clear()
        start = time.time()
        g = brain_graph.graph(self.conn, [a["name"] for a in dashboard.agent_roster()])
        self.assertLess(time.time() - start, 2.0)
        self.assertGreaterEqual(len(g["nodes"]), 1300)
        self.assertGreater(sum(1 for *_, kind in g["links"] if kind == "ref"), 1000)
        start = time.time()
        brain_graph.graph(self.conn, [a["name"] for a in dashboard.agent_roster()])
        self.assertLess(time.time() - start, 0.1)

    def test_skills_board_for_the_new_dashboard(self):
        data = json.loads(self.request("GET", "/api/skills", token=False)[1])
        names = [a["name"] for a in data["agents"]]
        self.assertIn("creative-director", names)
        self.assertIn("backlog", data["agents"][0])
        one = json.loads(self.request("GET", "/api/skills?agent=shopify", token=False)[1])
        self.assertEqual([a["name"] for a in one["agents"]], ["shopify"])
        self.assertEqual(self.request("GET", "/api/skills?agent=nobody", token=False)[0], 404)
        crew = json.loads(self.request("GET", "/api/team", token=False)[1])
        self.assertEqual(set(crew), {"generated", "teams", "sub_agents", "hires", "pace"})

    def test_approval_from_dashboard(self):
        aid = records.approval_request(self.conn, "spend", "Test budget A$100")
        self.assertEqual(self.request("POST", "/api/approval", {"id": aid, "approve": True}, token=False)[0], 403)
        status, raw = self.request("POST", "/api/approval", {"id": f"A{aid}", "approve": True})
        self.assertEqual((status, json.loads(raw)["status"]), (200, "approved"))
        self.assertEqual(self.request("POST", "/api/approval", {"id": aid, "approve": False})[0], 400)
        self.assertEqual(self.request("POST", "/api/approval", {"id": 999, "approve": True})[0], 400)

    def test_requests_need_token_and_loopback_host(self):
        self.assertEqual(self.request("POST", "/api/chat", {"text": "hi"}, token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:80")[0], 403)
        self.assertEqual(self.request("POST", "/api/chat", {"text": "  "})[0], 400)

    def test_conversation_streams_delegation_and_resumes(self):
        self.assertEqual(self.request("POST", "/api/chat", {"text": "research the posture corrector"})[0], 202)
        self.wait_idle()
        kinds = [e["type"] for e in self.events]
        for kind in ("say", "dispatch", "agent_activity", "agent_done", "reply"):
            self.assertIn(kind, kinds)
        dispatch = next(e for e in self.events if e["type"] == "dispatch")
        self.assertEqual(dispatch["agent"], "product-research")
        self.assertEqual(dispatch["description"], "Research posture corrector")
        activity = next(e for e in self.events if e["type"] == "agent_activity")
        self.assertIn("posture corrector reviews", activity["line"])
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=False", say["text"])
        conn = db.connect()
        self.assertEqual(db.get_meta(conn, "dashboard_session"), "sess-123")
        conn.close()

        # The next message goes to the same running process: no start-up wait.
        self.events.clear()
        self.request("POST", "/api/chat", {"text": "and the angles?"})
        self.wait_idle()
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=False. Turn=2", say["text"])

        # After a stop the process is gone; the next message resumes the saved conversation.
        self.assertEqual(self.request("POST", "/api/stop", {})[0], 200)
        self.events.clear()
        self.request("POST", "/api/chat", {"text": "carry on"})
        self.wait_idle()
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=True. Turn=1", say["text"])

        # New conversation forgets the session id (memory and journal stay).
        self.assertEqual(self.request("POST", "/api/new", {})[0], 200)
        conn = db.connect()
        self.assertFalse(db.get_meta(conn, "dashboard_session"))
        conn.close()

    def test_reply_is_spoken_sentence_by_sentence(self):
        self.use_fake("--fake-stream")
        self.request("POST", "/api/chat", {"text": "start the research"})
        self.wait_idle()
        chunks = [e["text"] for e in self.events if e["type"] == "say_chunk"]
        self.assertEqual(chunks, ["On it, sir.", "The research team is starting now.", "Shall I brief the creative team too?"])
        say = next(e for e in self.events if e["type"] == "say")
        self.assertTrue(say["streamed"])  # shown in full, but not spoken twice

    def test_pasted_images_reach_jarvis_and_are_saved(self):
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).decode()
        status, _ = self.request("POST", "/api/chat", {"text": "", "images": [{"media_type": "image/png", "data": png}]})
        self.assertEqual(status, 202)
        self.wait_idle()
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Images=1", say["text"])
        self.assertIn("files/uploads/", say["text"].replace("\\", "/"))  # the path, so agents can open it
        self.assertEqual(len(list((self.home / "files" / "uploads").glob("*.png"))), 1)
        bad = {"text": "x", "images": [{"media_type": "image/svg+xml", "data": png}]}
        self.assertEqual(self.request("POST", "/api/chat", bad)[0], 400)

    def use_fake(self, *flags):
        (self.home / "config.json").write_text(json.dumps({"claude_command": [*FAKE, *flags]}), encoding="utf-8")

    def test_logged_out_cli_is_explained_once(self):
        self.use_fake("--fake-logged-out")
        self.request("POST", "/api/chat", {"text": "hello Jarvis"})
        self.wait_idle()
        self.assertFalse([e for e in self.events if e["type"] == "say"])  # the raw CLI message is not read out
        reply = next(e for e in self.events if e["type"] == "reply")
        self.assertTrue(reply["is_error"] and reply["login"])
        self.assertIn("isn't signed in", reply["text"])
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertTrue(state["jarvis"]["login_needed"])

        # Once signed in, the next answer clears the warning.
        self.use_fake()
        self.events.clear()
        self.request("POST", "/api/chat", {"text": "hello again"})
        self.wait_idle()
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertFalse(state["jarvis"]["login_needed"])

    def test_missing_saved_conversation_starts_fresh(self):
        db.set_meta(self.conn, "dashboard_session", "gone-456")
        self.use_fake("--fake-missing-session")
        self.request("POST", "/api/chat", {"text": "hello"})
        self.wait_idle()
        self.assertFalse([e for e in self.events if e["type"] == "error"])
        say = next(e for e in self.events if e["type"] == "say")
        self.assertIn("Resumed=False", say["text"])
        conn = db.connect()
        self.assertEqual(db.get_meta(conn, "dashboard_session"), "sess-123")
        conn.close()

    def test_kokoro_voice(self):
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE, "tts_command": FAKE_TTS}),
                                              encoding="utf-8")
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertTrue(state["tts"]["available"])
        self.assertEqual(state["tts"]["voice"], "jarvis")
        self.assertIn("bm_george", [v["id"] for v in state["tts"]["voices"]])
        self.assertIn("jarvis", [v["id"] for v in state["tts"]["voices"]])
        self.assertEqual(self.request("POST", "/api/tts", {"text": "Good evening."}, token=False)[0], 403)
        status, wav = self.request("POST", "/api/tts", {"text": "Good evening, sir.", "voice": "bm_lewis"})
        self.assertEqual(status, 200)
        self.assertEqual(wav[:4], b"RIFF")
        # Once the worker reports its voices, only those (and presets built from them) are offered.
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertEqual([v["id"] for v in state["tts"]["voices"]],
                         ["jarvis_warm", "jarvis_crisp", "jarvis_b", "bm_george", "bm_lewis", "bm_daniel", "bf_emma"])
        status, wav = self.request("POST", "/api/tts", {"text": "Good evening, sir.", "voice": "jarvis_crisp"})
        self.assertEqual((status, wav[:4]), (200, b"RIFF"))
        # The cloned voice is only offered where its reference recording exists; otherwise it falls back to Kokoro.
        self.assertNotIn("jarvis-clone", [v["id"] for v in state["tts"]["voices"]])
        status, wav = self.request("POST", "/api/tts", {"text": "Good evening, sir.", "voice": "jarvis-clone"})
        self.assertEqual((status, wav[:4]), (200, b"RIFF"))
        self.assertEqual(self.request("POST", "/api/tts", {"text": "boom"})[0], 503)
        self.assertEqual(self.request("POST", "/api/tts", {"text": "  "})[0], 503)
        # A crashed worker is restarted on the next request.
        self.server.tts.proc.kill()
        self.server.tts.proc.wait()
        self.assertEqual(self.request("POST", "/api/tts", {"text": "Still here."})[0], 200)

    def test_voice_falls_back_without_kokoro(self):
        state = json.loads(self.request("GET", "/api/state", token=False)[1])
        self.assertFalse(state["tts"]["available"])
        self.assertEqual(self.request("POST", "/api/tts", {"text": "Hello"})[0], 503)

    def test_voice_prompt_is_jarvis_and_shell_safe(self):
        prompt = dashboard.VOICE_PROMPT
        for phrase in ("British", "sir", "approval", "who is doing what"):
            self.assertIn(phrase, prompt)
        self.assertFalse(set(prompt) & set('%^&|<>"'))

    def test_visuals_endpoint_and_stage_script(self):
        status, raw = self.request("POST", "/api/visuals", {"text": "We have 412 applications."})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["cards"][0]["value"], 412)
        status, js = self.request("GET", "/stage.js", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"/api/visuals", js)
        self.assertIn(b"/stage.js", self.request("GET", "/", token=False)[1])

    def test_missing_cli_is_reported(self):
        (self.home / "config.json").write_text(json.dumps({"claude_command": ["definitely-not-a-real-cli-xyz"]}),
                                              encoding="utf-8")
        self.request("POST", "/api/chat", {"text": "hello"})
        self.wait_idle()
        err = next(e for e in self.events if e["type"] == "error")
        self.assertIn("Could not start Claude Code", err["text"])

    # ------------------------------------------------------------ the control centre (T98)

    def wait_for(self, kind, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            ev = self.hub_q.get(timeout=timeout)
            self.events.append(ev)
            if ev.get("type") == kind:
                return ev
        self.fail(f"no {kind} event")

    def control_state(self):
        status, raw = self.request("GET", "/api/control/state", token=False)
        self.assertEqual(status, 200)
        return json.loads(raw)

    def test_main_page_loads_the_control_centre(self):
        status, html = self.request("GET", "/", token=False)
        self.assertIn(b'<script src="/control.js"></script>', html)
        self.assertIn(b'id="ctlBtn"', html)
        status, js = self.request("GET", "/control.js", token=False)
        self.assertEqual(status, 200)
        for api in (b"/api/control/state", b"/api/control/permission", b"/api/control/sessions", b"/api/control/screen",
                    b"/api/control/open", b"/api/control/job", b"/api/control/worker"):
            self.assertIn(api, js)

    def test_a_permission_prompt_waits_for_the_owner_on_the_page(self):
        self.assertIn("--permission-prompt-tool", " ".join(self._spawned_cmd()))
        self.request("POST", "/api/chat", {"text": "PERMIT change the title"})
        ask = self.wait_for("permission")
        self.assertEqual(ask["tool"], "mcp__Shopify__update-product")
        row = self.conn.execute("SELECT kind, status FROM approvals WHERE id = ?", (ask["approval"],)).fetchone()
        self.assertEqual(tuple(row), ("tool", "pending"))
        self.assertEqual(len(self.control_state()["permissions"]), 1)
        status, raw = self.request("POST", "/api/control/permission", {"id": ask["id"], "approve": True})
        self.assertEqual((status, json.loads(raw)["status"]), (200, "approved"))
        self.wait_idle()
        reply = next(e for e in self.events if e["type"] == "reply")
        self.assertEqual(reply["text"], "Permission: allow")
        self.assertEqual(self.control_state()["permissions"], [])

    def test_saying_no_rejects_a_waiting_prompt(self):
        self.request("POST", "/api/chat", {"text": "PERMIT please"})
        ask = self.wait_for("permission")
        status, raw = self.request("POST", "/api/chat", {"text": "No."})
        self.assertEqual(json.loads(raw)["answered"]["status"], "rejected")
        self.wait_idle()
        self.assertTrue(next(e for e in self.events if e["type"] == "reply")["text"].startswith("Permission: deny"))
        self.assertEqual(self.conn.execute("SELECT status FROM approvals WHERE id = ?", (ask["approval"],)).fetchone()[0],
                         "rejected")

    def test_a_question_from_jarvis_is_answered_on_the_page(self):
        self.request("POST", "/api/chat", {"text": "PERMIT ASK me"})
        ask = self.wait_for("permission")
        self.assertTrue(ask["question"])
        status, _ = self.request("POST", "/api/control/permission", {"id": ask["id"], "answers": {"Which colour?": "Blue"}})
        self.assertEqual(status, 200)
        self.wait_idle()
        self.assertIn('"Blue"', next(e for e in self.events if e["type"] == "reply")["text"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_tool_steps_results_and_commands_are_live(self):
        self.request("POST", "/api/chat", {"text": "STEP list the tasks"})
        self.wait_idle()
        tool = next(e for e in self.events if e["type"] == "jarvis_tool")
        done = next(e for e in self.events if e["type"] == "jarvis_tool_done")
        self.assertEqual((tool["id"], done["id"], done["ok"], done["out"]), ("tb1", "tb1", True, "T1 [todo] hello"))
        state = self.control_state()
        kinds = [(s["kind"], s["state"]) for s in state["steps"]]
        self.assertIn(("owner", "done"), kinds)
        self.assertIn(("tool", "done"), kinds)
        self.assertIn(("agent", "done"), kinds)
        self.assertIn("blake-testing", state["commands"]["slash"])

    def test_messages_sent_while_busy_wait_their_turn(self):
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE + ["--fake-stream"]}), encoding="utf-8")
        self.request("POST", "/api/chat", {"text": "first"})
        status, raw = self.request("POST", "/api/chat", {"text": "second"})
        self.assertEqual(status, 202)
        if json.loads(raw)["queued"]:
            self.wait_idle()  # the first turn
        self.wait_idle()
        replies = [e["text"] for e in self.events if e["type"] == "reply"]
        self.assertEqual(len(replies), 2)
        status, _ = self.request("POST", "/api/chat", {"text": "x", "queue": False})
        self.assertIn(status, (202, 409))

    def test_slash_command_output_is_marked_not_spoken(self):
        self.request("POST", "/api/chat", {"text": "/context"})
        self.wait_idle()
        self.assertTrue(next(e for e in self.events if e["type"] == "reply")["slash"])

    def test_attached_files_are_saved_and_named_in_the_message(self):
        data = base64.b64encode(b"sku,price\nA,10\n").decode()
        status, _ = self.request("POST", "/api/chat", {"text": "Read this", "files": [{"name": "prices.csv", "data": data}]})
        self.assertEqual(status, 202)
        self.wait_idle()
        said = next(e for e in self.events if e["type"] == "say")["text"]
        self.assertIn("prices.csv", said)
        self.assertTrue(list((self.home / "files" / "uploads").glob("*-prices.csv")))
        status, raw = self.request("POST", "/api/chat", {"text": "x", "files": [{"name": "evil.exe", "data": data}]})
        self.assertEqual(status, 400)

    def test_sessions_can_be_listed_read_and_switched(self):
        folder = self.home / "sessions"
        folder.mkdir()
        sid = "11111111-2222-3333-4444-555555555555"
        (folder / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "Hello from the desk"}}) + "\n",
                                             encoding="utf-8")
        cfg = {"claude_command": FAKE, "claude_sessions_dir": str(folder)}
        (self.home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        status, raw = self.request("GET", "/api/control/sessions", token=False)
        self.assertEqual(json.loads(raw)[0]["title"], "Hello from the desk")
        status, raw = self.request("GET", f"/api/control/transcript?id={sid}", token=False)
        self.assertEqual(json.loads(raw), [{"role": "owner", "text": "Hello from the desk", "ts": None}])
        self.assertEqual(self.request("POST", "/api/control/session", {"id": "../x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/control/session", {"id": sid}, token=False)[0], 403)
        status, _ = self.request("POST", "/api/control/session", {"id": sid})
        self.assertEqual(status, 200)
        self.assertEqual(db.get_meta(self.conn, "dashboard_session"), sid)
        self.request("POST", "/api/chat", {"text": "carry on"})
        self.wait_idle()
        self.assertIn("Resumed=True", next(e for e in self.events if e["type"] == "say")["text"])

    def test_the_screen_needs_the_page_key(self):
        self.assertEqual(self.request("GET", "/api/control/screen", token=False)[0], 403)
        from jarvis import control
        with unittest.mock.patch.object(control, "screenshot", return_value=b"\xff\xd8jpeg"):
            status, data = self.request("GET", f"/api/control/screen?k={self.server.token}", token=False)
        self.assertEqual((status, data), (200, b"\xff\xd8jpeg"))

    def test_pc_jobs_and_workers_from_the_page(self):
        with unittest.mock.patch("webbrowser.open") as web:
            status, raw = self.request("POST", "/api/control/open", {"target": "https://example.com"})
        self.assertEqual((status, json.loads(raw)["opened"]), (200, "web page https://example.com"))
        web.assert_called_once()
        self.assertEqual(self.request("POST", "/api/control/open", {"target": "nothing-like-this"})[0], 400)
        status, raw = self.request("POST", "/api/control/job", {"text": "Open Kalodata"})
        jid = json.loads(raw)["id"]
        self.assertEqual(status, 201)
        state = self.control_state()
        self.assertEqual((state["jobs"][0]["id"], state["jobs"][0]["source"], state["job_runner"]), (jid, "dashboard", False))
        status, raw = self.request("POST", "/api/control/job", {"cancel": jid})
        self.assertEqual(json.loads(raw)["status"], "cancelled")
        self.assertEqual(self.request("POST", "/api/control/worker", {"action": "stop", "id": "W99"})[0], 400)
        self.assertEqual(self.request("POST", "/api/control/worker", {"action": "fly"})[0], 400)
        self.assertEqual(self.request("GET", "/api/control/worker?id=W99", token=False)[0], 404)

    def _spawned_cmd(self):
        with unittest.mock.patch("subprocess.Popen") as popen:
            popen.return_value.stderr = []
            self.server.runner._spawn(["claude"], None)
        return popen.call_args[0][0]


class WakeWordTest(BrainTest):
    def test_hey_jarvis_reaches_the_dashboard_and_can_be_switched_off(self):
        self.init()
        fake = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_wake.py")]
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE, "wake_command": fake}), encoding="utf-8")
        server = dashboard.make_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        q = server.hub.subscribe()
        try:
            server.wake.start()
            ev = q.get(timeout=10)
            self.assertEqual(ev["type"], "wake")
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("POST", "/api/wake", json.dumps({"on": False}),
                         {"Host": f"127.0.0.1:{server.server_address[1]}", "X-Jarvis-Token": server.token})
            data = json.loads(conn.getresponse().read())
            conn.close()
            self.assertFalse(data["enabled"])
            self.assertFalse(data["on"])
            self.assertFalse(json.loads((self.home / "config.json").read_text(encoding="utf-8"))["wake_word"] is not False)
        finally:
            server.wake.stop()
            server.shutdown()
            server.server_close()


class WakeWordReviveTest(BrainTest):
    def test_a_worker_that_falls_over_at_start_up_is_started_again(self):
        self.init()
        flaky = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_wake_flaky.py"), str(self.home / "mic-failed")]
        (self.home / "config.json").write_text(json.dumps({"wake_command": flaky}), encoding="utf-8")
        ears = wakeword.WakeWord(lambda line: None)
        ears.REVIVE_AFTER = 0.2
        try:
            ears.start()
            deadline = time.time() + 15
            while not ears.ready and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(ears.ready, ears.error)
            self.assertEqual(ears.restarts, 0)
        finally:
            ears.stop()

    def test_switched_off_it_stays_off(self):
        self.init()
        flaky = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_wake_flaky.py"), str(self.home / "mic-failed")]
        (self.home / "config.json").write_text(json.dumps({"wake_command": flaky, "wake_word": False}), encoding="utf-8")
        ears = wakeword.WakeWord(lambda line: None)
        ears.REVIVE_AFTER = 0.2
        ears.start()
        time.sleep(1.5)
        self.assertFalse(ears.ready)
        self.assertTrue((self.home / "mic-failed").exists())
        ears.stop()


class LocalEarsTest(BrainTest):
    def test_spoken_requests_reach_jarvis_and_the_mic_button_listens_locally(self):
        self.init()
        fake = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_ears.py")]
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE, "wake_command": fake}), encoding="utf-8")
        server = dashboard.make_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        q = server.hub.subscribe()
        events = []

        def until(pred, timeout=15):
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    ev = q.get(timeout=0.5)
                except Exception:
                    continue
                events.append(ev)
                if pred(ev):
                    return ev
            self.fail(f"never saw it; events: {[e.get('type') for e in events]}")

        try:
            server.wake.start()
            until(lambda e: e["type"] == "ears" and e["state"] == "listening")
            owner = until(lambda e: e["type"] == "owner")
            self.assertEqual((owner["text"], owner["via"]), ("how are the ads going", "voice"))
            until(lambda e: e["type"] == "status" and e["status"] == "idle")
            self.assertTrue(server.wake.info()["stt"])
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("POST", "/api/listen", json.dumps({"followup": True}),
                         {"Host": f"127.0.0.1:{server.server_address[1]}", "X-Jarvis-Token": server.token})
            self.assertEqual(conn.getresponse().status, 202)
            conn.close()
            self.assertEqual(until(lambda e: e["type"] == "owner")["text"], "and the store")
        finally:
            server.wake.stop()
            server.runner.close()
            server.shutdown()
            server.server_close()


class StreamEventTest(BrainTest):
    def test_a_plain_text_message_field_does_not_kill_the_reply(self):
        self.init()
        hub = dashboard.Hub()
        runner = dashboard.Runner(hub)
        runner._handle({"type": "system", "subtype": "notice", "message": "Some plain text"}, {})


class EmptyResultTest(BrainTest):
    def test_a_reply_with_nothing_to_say_tells_the_owner(self):
        self.init()
        hub = dashboard.Hub()
        q = hub.subscribe()
        dashboard.Runner(hub)._handle({"type": "result", "subtype": "success", "result": "", "session_id": "s"}, {})
        self.assertEqual(q.get(timeout=1)["type"], "error")


class FrozenReplyTest(BrainTest):
    def test_a_frozen_claude_is_ended_and_the_message_retried(self):
        self.init()
        (self.home / "config.json").write_text(json.dumps({
            "claude_command": [*FAKE, "--fake-freeze", "--fake-marker", str(self.home / "froze")],
            "reply_timeout_seconds": 1}), encoding="utf-8")
        hub = dashboard.Hub()
        q = hub.subscribe()
        runner = dashboard.Runner(hub)
        conn = db.connect()
        db.set_meta(conn, "dashboard_session", "sess-123")
        conn.close()
        runner.proc = None
        try:
            runner.start("are you there?")
            deadline, events = time.time() + 20, []
            while time.time() < deadline:
                ev = q.get(timeout=20)
                events.append(ev)
                if ev == {"type": "status", "status": "idle"}:
                    break
        finally:
            runner.close()
        self.assertFalse(runner.busy)
        self.assertTrue(any("are you there?" in str(e.get("text")) for e in events), events)


class LongMessageTest(BrainTest):
    PHOTO = {"media_type": "image/png", "data": base64.b64encode(b"x" * 1_000_000).decode()}  # more than the pipe holds

    def runner(self, flag):
        self.init()
        (self.home / "config.json").write_text(json.dumps({
            "claude_command": [*FAKE, flag, "--fake-marker", str(self.home / "first")],
            "reply_timeout_seconds": 1}), encoding="utf-8")
        conn = db.connect()
        db.set_meta(conn, "dashboard_session", "sess-123")
        conn.close()
        hub = dashboard.Hub()
        return dashboard.Runner(hub), hub.subscribe()

    def turn(self, runner, q, *args):
        runner.start(*args)
        deadline, events = time.time() + 20, []
        while time.time() < deadline:
            ev = q.get(timeout=20)
            events.append(ev)
            if ev == {"type": "status", "status": "idle"}:
                break
        return events

    def test_a_photo_claude_never_reads_is_still_caught_by_the_watchdog(self):
        runner, q = self.runner("--fake-deaf")
        try:
            events = self.turn(runner, q, "look at this", [self.PHOTO])
        finally:
            runner.close()
        self.assertTrue(any("Images=1" in str(e.get("text")) for e in events), events)

    def test_a_photo_sent_while_claude_waits_for_its_output_to_be_read(self):
        runner, q = self.runner("--fake-chatty")
        try:
            self.turn(runner, q, "first")
            events = self.turn(runner, q, "look at this", [self.PHOTO])
        finally:
            runner.close()
        self.assertTrue(any("Images=1" in str(e.get("text")) for e in events), events)


class LostConversationTest(BrainTest):
    def test_a_lost_conversation_starts_afresh_instead_of_going_silent(self):
        self.init()
        (self.home / "config.json").write_text(json.dumps({"claude_command": [*FAKE, "--fake-lost-session"]}),
                                               encoding="utf-8")
        conn = db.connect()
        db.set_meta(conn, "dashboard_session", "gone-111")
        conn.close()
        hub = dashboard.Hub()
        q = hub.subscribe()
        runner = dashboard.Runner(hub)
        try:
            runner.start("are you there?")
            deadline, events = time.time() + 20, []
            while time.time() < deadline:
                ev = q.get(timeout=20)
                events.append(ev)
                if ev == {"type": "status", "status": "idle"}:
                    break
        finally:
            runner.close()
        self.assertTrue(any("are you there?" in str(e.get("text")) for e in events), events)
        self.assertFalse(any(e.get("type") == "reply" and e.get("is_error") for e in events), events)
        conn = db.connect()
        self.assertEqual(db.get_meta(conn, "dashboard_session"), "sess-123")
        conn.close()


class PageTokenTest(BrainTest):
    def test_the_page_key_survives_a_restart(self):
        self.init()
        first = dashboard.make_server(port=0)
        second = dashboard.make_server(port=0)
        try:
            self.assertEqual(first.token, second.token)  # an open chat window keeps working after a restart
            self.assertGreater(len(first.token), 20)
        finally:
            first.server_close()
            second.server_close()

