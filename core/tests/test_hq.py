import http.client
import json
import threading

from helpers import BrainTest

from jarvis import dashboard, hq, records


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
        status, js = self.get("/hq.js")
        self.assertEqual(status, 200)
        self.assertIn(b"const Feed", js)
        status, raw = self.get("/api/hq")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(raw)["teams"]), 8)
        for path in ("/", "/console", "/chat"):  # the pages the owner uses are untouched
            self.assertEqual(self.get(path)[0], 200, path)

    def test_file_preview_route(self):
        (self.home / "work").mkdir(exist_ok=True)
        (self.home / "work" / "brief.md").write_text("Angles", encoding="utf-8")
        self.assertEqual(self.get("/api/hq/file?path=brain/work/brief.md"), (200, b"Angles"))
        self.assertEqual(self.get("/api/hq/file?path=brain/work/gone.md")[0], 204)
        self.assertEqual(self.get("/api/hq/file?path=../README.md")[0], 204)
