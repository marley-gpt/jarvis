import http.client
import json
import threading

from helpers import BrainTest

from jarvis import brief, dashboard, records, roster


class RosterTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()

    def test_agents_tools_skills_and_models(self):
        agents = roster.agents()
        self.assertEqual(len(agents), 12)
        self.assertIn("Shopify store (read-only)", agents["store-qa"]["connections"])
        self.assertIn("Chrome browser", agents["store-qa"]["connections"])
        self.assertIn("jarvis-memory", agents["creative-director"]["skills"])
        self.assertIn("blake-creatives", agents["creative-director"]["skills"])
        self.assertEqual(roster.model_alias("claude-fable-5-1"), "fable")
        self.assertEqual(roster.model_alias(None), "inherit")
        self.assertNotIn("fable", [m for m, _ in roster.RECOMMENDED.values()])   # Fable is the owner's call (D77)

    def test_week_daily_profit_and_connector_parsing(self):
        records.money_add(self.conn, "income", "sales", 120, currency="AUD")
        records.money_add(self.conn, "expense", "ads", 50, currency="AUD")
        daily = roster.daily_profit(self.conn)
        self.assertEqual(len(daily), 14)
        self.assertEqual(daily[-1]["profit"], 70)
        self.assertEqual((roster.week(self.conn)["target"], roster.week(self.conn)["profit"]), (380.0, 70))
        m = roster.MCP_LINE.match("plugin:github:github: https://api.githubcopilot.com/mcp/ (HTTP) - ✔ Connected")
        self.assertEqual(m["name"], "plugin:github:github")
        self.assertIsNone(roster.MCP_LINE.match("Checking MCP server health…"))
        self.assertIsNone(roster.connectors()["servers"])  # nothing is checked until asked

    def test_metrics_snapshot_round_trip(self):
        self.assertIsNone(roster.metrics())
        snap = roster.metrics_set({"period": "today", "shopify": {"orders": 3, "revenue": 129.0, "bogus": "x"},
                                   "meta": {"spend": 40.5, "roas": 3.19}, "products": [{"name": "brush", "spend": 40.5, "orders": 3}]})
        self.assertTrue(snap["pulled"])
        self.assertEqual(snap["shopify"], {"orders": 3, "revenue": 129.0})
        self.assertEqual(roster.metrics()["meta"]["roas"], 3.19)
        with self.assertRaises(ValueError):
            roster.metrics_set({"shopify": "nope"})
        out = self.run_cli("metrics", "show")
        self.assertIn("orders=3", out.stdout)

    def test_models_and_tasks_from_the_page(self):
        agents_dir = self.home / "agents"
        agents_dir.mkdir()
        (agents_dir / "shopify.md").write_text("---\nname: shopify\ndescription: Store.\nmodel: sonnet\n---\nBody\n", encoding="utf-8")
        old = roster.AGENTS_DIR
        roster.AGENTS_DIR = agents_dir
        try:
            self.assertEqual(roster.set_agent_model("shopify", "opus"), "opus")
            self.assertIn("model: opus", (agents_dir / "shopify.md").read_text(encoding="utf-8"))
            self.assertEqual(roster.set_agent_model("shopify", "fable"), "claude-fable-5-1")
            self.assertEqual(roster.agents()["shopify"]["model"], "fable")
            with self.assertRaises(ValueError):
                roster.set_agent_model("shopify", "gpt")
            with self.assertRaises(ValueError):
                roster.set_agent_model("../CLAUDE", "opus")
            tid = roster.add_task(self.conn, "  Draft the brush PDP  ", agent="shopify", model="haiku")
        finally:
            roster.AGENTS_DIR = old
        self.assertEqual(roster.task_models(), {f"T{tid}": "haiku"})
        self.assertIn("(model: haiku", brief.task_line(records.task_list(self.conn, open_only=True)[0]))
        roster.set_task_model(tid, "inherit")
        self.assertEqual(roster.task_models(), {})
        with self.assertRaises(ValueError):
            roster.add_task(self.conn, "   ")

    def test_preview_reads_brain_files_only(self):
        work = self.home / "work" / "x"
        work.mkdir(parents=True)
        (work / "T1-pdp.md").write_text("# PDP draft\nHello", encoding="utf-8")
        (work / "ad.png").write_bytes(b"\x89PNG fake")
        (self.home / "secrets").mkdir(exist_ok=True)
        (self.home / "secrets" / "keys.txt").write_text("nope", encoding="utf-8")
        body, ctype = roster.preview("brain/work/x/T1-pdp.md")
        self.assertEqual(body.decode(), "# PDP draft\nHello")
        self.assertTrue(ctype.startswith("text/plain"))
        self.assertEqual(roster.preview("work/x/ad.png")[1], "image/png")
        self.assertIsNone(roster.preview("brain/../../core/jarvis/roster.py"))
        self.assertIsNone(roster.preview("secrets/keys.txt"))
        self.assertIsNone(roster.preview("jarvis.db"))
        self.assertIsNone(roster.preview("brain/work/x/missing.md"))
        self.assertIsNone(roster.preview(""))


class RosterRoutesTest(BrainTest):
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

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "X-Jarvis-Token": self.server.token})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_team_map_shows_every_agent_by_department(self):
        from jarvis import orgchart
        tid = records.task_add(self.conn, "Brush launch")
        a = records.task_add(self.conn, "Research the brush", parent=tid, agent="market-research")
        records.task_update(self.conn, a, status="done", summary="done")
        b = records.task_add(self.conn, "Statics for the brush", parent=tid, agent="creative-director")
        status, raw = self.call("GET", "/api/teammap")
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual([d["id"] for d in data["departments"]][:6], ["research", "creative", "store", "ads", "money", "ops"])
        placed = [n for d in data["departments"] for n in d["agents"]]
        self.assertEqual(sorted(placed), sorted(p.stem for p in orgchart.AGENTS_DIR.glob("*.md")))
        self.assertEqual(len(placed), len(set(placed)))
        shop = data["agents"]["shopify"]
        self.assertIn("blake-shopify-cro", shop["skills"])
        self.assertTrue(shop["sop"] and shop["sop"][0]["title"] == "Role")
        self.assertEqual([t["id"] for t in data["agents"]["creative-director"]["open"]], [f"T{b}"])
        hand = data["handoffs"][-1]
        self.assertEqual((hand["from"], hand["to"], hand["task"]), ("market-research", "creative-director", f"T{b}"))
        self.assertIn("open_projects", data["jarvis"])
        # The team page asks for these: the map, an agent's skill levels, and its questions go through /api/chat.
        js = (dashboard.STATIC / "page-team.js").read_text(encoding="utf-8")
        for needle in ("/api/teammap", "/api/skills?agent=", "/api/agent-model", "App.say(", "Ask the ${sel} agent"):
            self.assertIn(needle, js)

    def test_conversation_model_and_connector_check(self):
        status, raw = self.call("POST", "/api/conversation", {"model": "sonnet", "effort": "low"})
        self.assertEqual((status, json.loads(raw)["model"], json.loads(raw)["effort"]), (200, "sonnet", "low"))
        self.assertEqual(self.call("POST", "/api/conversation", {"model": "gpt"})[0], 400)
        self.assertEqual(self.call("POST", "/api/agent-model", {"agent": "nobody", "model": "opus"})[0], 400)
        self.assertEqual(self.call("GET", "/api/file?path=brain/work/none.md")[0], 204)
