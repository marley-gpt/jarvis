import http.client
import json
import threading

from helpers import BrainTest

from jarvis import brief, dashboard, hq, records


class HQDataTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()

    def test_every_agent_has_exactly_one_team(self):
        agents = hq.agents()
        placed = [n for t in hq.TEAMS for n in t["agents"]]
        self.assertEqual(len(hq.TEAMS), 8)
        self.assertEqual(sorted(placed), sorted(agents))
        self.assertIn("Shopify store (read-only)", agents["store-qa"]["connections"])
        self.assertIn("Chrome browser", agents["store-qa"]["connections"])
        self.assertIn("jarvis-memory", agents["creative-director"]["skills"])
        self.assertIn("blake-creatives", agents["creative-director"]["skills"])

    def test_recent_work_and_no_activity_log_yet(self):
        tid = records.task_add(self.conn, "Draft the PDP", agent="shopify")
        records.task_update(self.conn, tid, status="done", deliverable="brain/work/x/T1-pdp.md")
        data = hq.data(self.conn)
        self.assertEqual(data["recent"]["shopify"][0]["deliverable"], "brain/work/x/T1-pdp.md")
        self.assertNotIn("creative-director", data["recent"])
        if data["activity"] is not None:  # phase 1 installed: it must carry the shape the page reads
            self.assertIn("agents", data["activity"])

    def test_board_and_week(self):
        a = records.task_add(self.conn, "Write hooks", agent="creative-director")
        b = records.task_add(self.conn, "Check the PDP", agent="store-qa")
        records.task_update(self.conn, b, status="done")
        data = hq.data(self.conn)
        self.assertIn(a, [t["id"] for t in data["board"]["open"]])
        self.assertEqual([t["id"] for t in data["board"]["done"]], [b])
        self.assertEqual(data["week"]["target"], 380.0)
        self.assertEqual(data["week"]["profit"], 0)
        self.assertIsNone(data["milestone"])  # not set: the page says so

    def test_daily_profit_and_connector_parsing(self):
        records.money_add(self.conn, "income", "sales", 120, currency="AUD")
        records.money_add(self.conn, "expense", "ads", 50, currency="AUD")
        daily = hq.daily_profit(self.conn)
        self.assertEqual(len(daily), 14)
        self.assertEqual(daily[-1]["profit"], 70)
        m = hq.MCP_LINE.match("plugin:github:github: https://api.githubcopilot.com/mcp/ (HTTP) - ✔ Connected")
        self.assertEqual(m["name"], "plugin:github:github")
        self.assertIsNone(hq.MCP_LINE.match("Checking MCP server health…"))
        self.assertIsNone(hq.connectors()["servers"])  # nothing is checked until asked

    def test_metrics_snapshot_round_trip(self):
        self.assertIsNone(hq.metrics())
        snap = hq.metrics_set({"period": "today", "shopify": {"orders": 3, "revenue": 129.0, "bogus": "x"},
                               "meta": {"spend": 40.5, "roas": 3.19}, "products": [{"name": "brush", "spend": 40.5, "orders": 3}]})
        self.assertTrue(snap["pulled"])
        self.assertEqual(snap["shopify"], {"orders": 3, "revenue": 129.0})
        self.assertEqual(hq.metrics()["meta"]["roas"], 3.19)
        self.assertEqual(hq.data(self.conn)["metrics"]["products"][0]["name"], "brush")
        with self.assertRaises(ValueError):
            hq.metrics_set({"shopify": "nope"})
        out = self.run_cli("metrics", "show")
        self.assertIn("orders=3", out.stdout)

    def test_control_from_hq_models_and_tasks(self):
        agents_dir = self.home / "agents"
        agents_dir.mkdir()
        (agents_dir / "shopify.md").write_text("---\nname: shopify\ndescription: Store.\nmodel: sonnet\n---\nBody\n", encoding="utf-8")
        old = hq.AGENTS_DIR
        hq.AGENTS_DIR = agents_dir
        try:
            self.assertEqual(hq.set_agent_model("shopify", "opus"), "opus")
            self.assertIn("model: opus", (agents_dir / "shopify.md").read_text(encoding="utf-8"))
            self.assertEqual(hq.set_agent_model("shopify", "fable"), "claude-fable-5-1")
            self.assertEqual(hq.agents()["shopify"]["model"], "fable")
            with self.assertRaises(ValueError):
                hq.set_agent_model("shopify", "gpt")
            with self.assertRaises(ValueError):
                hq.set_agent_model("../CLAUDE", "opus")
            tid = hq.add_task(self.conn, "  Draft the brush PDP  ", agent="shopify", model="haiku")
        finally:
            hq.AGENTS_DIR = old
        self.assertEqual(hq.task_models(), {f"T{tid}": "haiku"})
        self.assertIn("(model: haiku", brief.task_line(records.task_list(self.conn, open_only=True)[0]))
        hq.set_task_model(tid, "inherit")
        self.assertEqual(hq.task_models(), {})
        with self.assertRaises(ValueError):
            hq.add_task(self.conn, "   ")

    def test_preview_reads_brain_files_only(self):
        work = self.home / "work" / "x"
        work.mkdir(parents=True)
        (work / "T1-pdp.md").write_text("# PDP draft\nHello", encoding="utf-8")
        (work / "ad.png").write_bytes(b"\x89PNG fake")
        (self.home / "secrets").mkdir(exist_ok=True)
        (self.home / "secrets" / "keys.txt").write_text("nope", encoding="utf-8")
        body, ctype = hq.preview("brain/work/x/T1-pdp.md")
        self.assertEqual(body.decode(), "# PDP draft\nHello")
        self.assertTrue(ctype.startswith("text/plain"))
        self.assertEqual(hq.preview("work/x/ad.png")[1], "image/png")
        self.assertIsNone(hq.preview("brain/../../core/jarvis/hq.py"))
        self.assertIsNone(hq.preview("secrets/keys.txt"))
        self.assertIsNone(hq.preview("jarvis.db"))
        self.assertIsNone(hq.preview("brain/work/x/missing.md"))
        self.assertIsNone(hq.preview(""))


class HQRoutesTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        (self.home / "config.json").write_text(json.dumps({"wake_word": False}), encoding="utf-8")
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

    def test_page_script_and_data(self):
        status, html = self.get("/hq")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), html)
        self.assertEqual(self.get("/")[1], html)  # the app is the main page
        status, js = self.get("/hq.js")
        self.assertEqual(status, 200)
        self.assertIn(b"const Feed", js)
        self.assertEqual(self.get("/hq-scene.js")[0], 200)
        status, raw = self.get("/api/hq")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(raw)["teams"]), 8)
        for path in ("/office", "/console", "/chat"):  # the earlier pages stay available
            self.assertEqual(self.get(path)[0], 200, path)

    def test_control_routes(self):
        import http.client as hc
        def post(path, body):
            conn = hc.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("POST", path, json.dumps(body), {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json",
                                                           "X-Jarvis-Token": self.server.token})
            resp = conn.getresponse(); data = json.loads(resp.read()); conn.close()
            return resp.status, data
        status, data = post("/api/hq/task", {"title": "Check the cart drawer", "agent": "store-qa", "model": "sonnet"})
        self.assertEqual(status, 201)
        self.assertEqual(post("/api/hq/task-model", {"task": data["id"], "model": "opus"}), (200, {"ok": True, "task_models": {f"T{data['id']}": "opus"}}))
        self.assertEqual(post("/api/hq/task", {"title": ""})[0], 400)
        self.assertEqual(post("/api/hq/agent-model", {"agent": "nope", "model": "opus"})[0], 400)
        self.assertEqual(json.loads(self.get("/api/hq")[1])["task_models"], {f"T{data['id']}": "opus"})

    def test_file_preview_route(self):
        (self.home / "work").mkdir(exist_ok=True)
        (self.home / "work" / "brief.md").write_text("Angles", encoding="utf-8")
        self.assertEqual(self.get("/api/hq/file?path=brain/work/brief.md"), (200, b"Angles"))
        self.assertEqual(self.get("/api/hq/file?path=brain/work/gone.md")[0], 204)
        self.assertEqual(self.get("/api/hq/file?path=../README.md")[0], 204)
