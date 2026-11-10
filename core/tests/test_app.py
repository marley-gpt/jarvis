"""The app (T110): one shell for every page, one state for all of them on the event stream, the product-testing
cockpit's sections, and the upgrades that came with it (inbox, plans, worker models, debate mode)."""
import http.client
import json
import os
import re
import sys
import threading
import time
import unittest.mock

from helpers import CORE, BrainTest

from jarvis import cockpit, dashboard, jobs, records, roster, work

FAKE = [sys.executable, str(CORE / "tests" / "fixtures" / "fake_claude.py")]
UI = CORE / "dashboard"


def fake_spawn(run, prompt, resume):
    """A worker that "runs" as this test process: alive, with no Claude Code started."""
    run.update(pid=os.getpid(), status="running", attempts=run.get("attempts", 0) + 1)
    work._save(run)


class AppTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        (self.home / "config.json").write_text(json.dumps({"claude_command": FAKE, "wake_word": False}), encoding="utf-8")
        self.server = dashboard.make_server(port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.runner.close()
        self.server.tts.stop()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-Jarvis-Token"] = self.server.token
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data, resp

    def get(self, path):
        status, data, _ = self.request("GET", path, token=False)
        return status, data

    def post(self, path, body):
        status, data, _ = self.request("POST", path, body)
        return status, json.loads(data or b"{}")

    def sections(self):
        return json.loads(self.get("/api/app?fresh=1")[1])["sections"]

    # ------------------------------------------------------------ one app, a few pages, no orphans

    def test_every_address_serves_the_one_shell(self):
        status, home = self.get("/")
        self.assertEqual(status, 200)
        for needle in (self.server.token.encode(), b'<script src="/jarvis.js"></script>', b'<script src="/app.js"></script>',
                       b'<script src="/page-testing.js"></script>', b'<script src="/live.js"></script>', b'href="/app.css"',
                       b'id="talk"', b'id="inbox"', b'id="kpis"', b'id="nav"'):
            self.assertIn(needle, home)
        for path in ("/team", "/work", "/money", "/brain", "/system", "/testing", "/work/T110", "/team/creative-director",
                     "/brain/M-261110-cdd9"):
            status, html = self.get(path)
            self.assertEqual((status, html), (200, home), path)   # the same frame: the page inside changes without a reload
        # Every id the shell's script looks up is in the shell.
        js = (UI / "app.js").read_bytes()
        ids = set(re.findall(rb'id="([^"]+)"', home))
        for found in set(re.findall(rb'\$\("#([A-Za-z0-9]+)', js)):
            self.assertIn(found, ids, found)

    def test_each_page_has_its_script_and_nothing_else_is_served(self):
        self.assertEqual(sorted(dashboard.APP_PAGES), ["brain", "money", "system", "team", "testing", "work"])
        for name in dashboard.APP_PAGES:
            status, js = self.get(f"/page-{name}.js")
            self.assertEqual(status, 200, name)
            self.assertIn(f'App.register("{name}"'.encode(), js)
            self.assertIn(f'href="/{"" if name == "testing" else name}"'.encode(), self.get("/")[1])
        self.assertIn(b"window.J = J", self.get("/jarvis.js")[1])
        self.assertIn(b"container: main", self.get("/app.css")[1])
        for path in ("/nope", "/page-nothing.js", "/README.md", "/call.html", "/..%2fjarvis%2fdb.py", "/api/nope"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_the_3d_hq_the_chat_and_the_old_pages_are_gone(self):
        for name in ("hq.html", "hq.js", "hq-scene.js", "office.html", "chat.html", "console.html", "stage.html", "stage.js",
                     "brain.html", "brain.js", "index.html", "swarm.js", "show.js", "team.js", "control.js"):
            self.assertFalse((UI / name).exists(), name)
            self.assertEqual(self.get("/" + name)[0], 404, name)
        for name in ("hq.py", "hq_assets.py", "brain_graph.py"):
            self.assertFalse((CORE / "jarvis" / name).exists(), name)
        # An old bookmark or shortcut lands on the app.
        for path in dashboard.RETIRED:
            status, _, resp = self.request("GET", path, token=False)
            self.assertEqual((status, resp.getheader("Location")), (302, "/"), path)
        for gone in ("/api/hq", "/api/hq/assets", "/api/state", "/api/brain/graph", "/api/swarm", "/api/team", "/api/activity"):
            self.assertEqual(self.get(gone)[0], 404, gone)
        self.assertEqual(self.post("/api/visuals", {"text": "x"})[0], 404)
        self.assertNotEqual(self.run_cli("hq", "assets", check=False).returncode, 0)
        self.assertTrue((UI / "call.html").exists())   # phone calls keep their page (served by the call server)
        app = (CORE / "jarvis_app.pyw").read_text(encoding="utf-8")
        self.assertNotIn('("JARVIS Chat"', app)   # one shortcut opens the one app
        self.assertIn("JARVIS Chat.lnk", app)     # and the old one is removed on install

    # ------------------------------------------------------------ one state for every page

    def test_the_state_arrives_whole_then_only_what_changed(self):
        self.assertEqual(set(self.sections()), {"kpis", "pipeline", "tests", "inbox", "work", "live", "money", "capacity",
                                                "brain", "system"})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()

        def event():
            while True:
                line = resp.fp.readline()
                if line.startswith(b"data: "):
                    return json.loads(line[6:])

        first = event()
        self.assertEqual((first["type"], first["full"]), ("state", True))
        self.assertEqual(len(first["sections"]), 10)
        records.task_add(self.conn, "Launch the foot file")   # anything written to the brain reaches the page by itself
        changed = event()
        while changed["type"] != "state":
            changed = event()
        conn.close()
        self.assertIn("work", changed["sections"])
        self.assertEqual(changed["sections"]["work"]["plans"][0]["title"], "Launch the foot file")
        for same in ("tests", "capacity", "money", "pipeline"):
            self.assertNotIn(same, changed["sections"])
        self.assertNotIn("full", changed)

    def test_a_rebuild_with_nothing_new_publishes_nothing(self):
        feed = cockpit.Feed(self.server)
        self.assertEqual(len(feed.build()), 10)
        self.assertEqual(feed.build(), {})
        records.product_add(self.conn, "Foot File")
        self.assertEqual(set(feed.build()), {"pipeline"})

    def test_state_carries_what_the_shell_needs(self):
        s = self.sections()
        self.assertEqual(len(s["system"]["agents"]), 12)
        self.assertTrue(s["system"]["claude"])
        self.assertFalse(s["system"]["tts"]["available"])
        self.assertEqual(s["kpis"]["weekly_target_aud"], 380.0)
        self.assertEqual((s["kpis"]["inbox"], s["kpis"]["agents_live"], s["kpis"]["live"]), (0, 0, False))
        self.assertTrue(s["kpis"]["why_not_live"])            # no pull yet: the strip says so, it never shows stale as live
        self.assertEqual(len(s["money"]["daily"]), 14)
        self.assertEqual(s["capacity"]["ceiling_pct"], 90.0)  # the capacity meter, from bin/jarvis pace
        self.assertIn("allowed_runs", s["capacity"])
        self.assertTrue(s["system"]["routines"]["routines"])
        self.assertEqual(s["brain"]["total"], sum(c["n"] for c in s["brain"]["categories"]))

    # ------------------------------------------------------------ the testing pipeline

    def card(self, slug="foot-file"):
        return next(p for p in self.sections()["pipeline"]["products"] if p["slug"] == slug)

    def test_a_product_moves_along_the_rail(self):
        slug = records.product_add(self.conn, "Foot File")
        c = self.card()
        self.assertEqual([s["key"] for s in c["stages"]], ["candidate", "research", "creatives", "pdp", "campaign", "approval", "live", "verdict"])
        self.assertEqual((c["at"], c["next"]["kind"], c["round"], c["blockers"]), ("candidate", "research", 1, []))

        parent = records.task_add(self.conn, "Launch test: Foot File", product=slug)
        research = records.task_add(self.conn, "Pre-test gate", product=slug, parent=parent, agent="product-research")
        statics = records.task_add(self.conn, "15 statics", product=slug, parent=parent, agent="creative-director")
        c = self.card()
        self.assertEqual((c["at"], c["next"]["kind"], c["next"]["task"]), ("research", "start", f"T{research}"))
        self.assertIn("Nothing is running", c["blockers"][0])     # queued work nobody has started is a blocker

        records.task_update(self.conn, research, status="done", summary="go")
        records.task_update(self.conn, statics, status="in_progress")
        c = self.card()
        states = {s["key"]: s["state"] for s in c["stages"]}
        self.assertEqual((states["research"], states["creatives"], c["at"], c["next"]["kind"]), ("done", "active", "creatives", "watch"))
        self.assertEqual(c["blockers"], [])
        self.assertTrue(c["moving"])

        records.task_update(self.conn, statics, status="done")
        aid = records.approval_request(self.conn, "launch", "Launch the Foot File test at US$50 a day", task=parent, amount=150, currency="USD")
        c = self.card()
        self.assertEqual((c["at"], c["next"]["kind"], c["next"]["approval"]), ("approval", "approve", aid))
        self.assertTrue(any(f"A{aid} is waiting for you" in b for b in c["blockers"]))
        item = next(i for i in self.sections()["inbox"] if i["kind"] == "approval")
        self.assertEqual((item["approval"], item["gate"], item["product"], item["amount"]), (aid, "launch", slug, 150))

        # One tap on the page approves it; the card moves on and the inbox clears.
        self.assertEqual(self.post("/api/approval", {"id": aid, "approve": True})[0], 200)
        self.assertEqual(self.sections()["inbox"], [])
        xid = records.test_add(self.conn, slug, "People with cracked heels will buy it")
        records.test_update(self.conn, xid, status="running")
        c = self.card()
        states = {s["key"]: s["state"] for s in c["stages"]}
        self.assertEqual((c["at"], states["approval"], states["live"], c["next"]["test"]), ("live", "done", "active", f"X{xid}"))

        # The test is called: that closes round one, and the card waits for the owner's call on what is next.
        records.test_update(self.conn, xid, status="stopped", verdict="killed: no buying intent")
        c = self.card()
        self.assertEqual((c["round"], c["at"], c["next"]["kind"], c["last_test"]["id"]), (2, "verdict", "debate", f"X{xid}"))
        self.assertEqual({s["key"]: s["state"] for s in c["stages"]}["creatives"], "carried")

    def test_archived_products_and_the_hunt_for_the_next_one(self):
        slug = records.product_add(self.conn, "Beetroot")
        records.product_update(self.conn, slug, stage="archived")
        hunt = records.task_add(self.conn, "Product hunt: five candidates", agent="product-research")
        pl = self.sections()["pipeline"]
        self.assertEqual(([p["slug"] for p in pl["products"]], [p["slug"] for p in pl["archived"]]), ([], [slug]))
        self.assertEqual([h["id"] for h in pl["hunts"]], [f"T{hunt}"])

    # ------------------------------------------------------------ tests against Blake's day rules

    def test_day_rules_and_insufficient_data(self):
        self.assertEqual(cockpit.day_call(1, 0, 0, 0, None, 1.4)[0], "cut")
        self.assertEqual(cockpit.day_call(1, 3, 1, 0, None, 1.4)[0], "continue")
        self.assertEqual(cockpit.day_call(2, 4, 0, 0, None, 1.4)[0], "cut")
        self.assertEqual(cockpit.day_call(2, 4, 3, 0, None, 1.4)[0], "diagnose")
        self.assertEqual(cockpit.day_call(3, 9, 4, 0, None, 1.4)[0], "cut")
        self.assertEqual(cockpit.day_call(3, 9, 4, 2, 1.1, 1.4)[0], "continue")
        self.assertEqual(cockpit.day_call(3, 9, 4, 5, 1.9, 1.4)[0], "scale")
        self.assertEqual(cockpit.day_call(3, 9, 4, 5, 1.9, None)[0], "continue")   # no breakeven line: never called profitable

        slug = records.product_add(self.conn, "Foot File")
        xid = records.test_add(self.conn, slug, "Cracked heels")
        t = self.sections()["tests"][0]
        self.assertEqual((t["id"], t["days"], t["sufficient"], t["call"]), (f"X{xid}", [], False, None))
        records.test_update(self.conn, xid, status="running")
        records.test_metric(self.conn, xid, "1", spend=11, impressions=300, clicks=9)                       # a partial read
        records.test_metric(self.conn, xid, "1", spend=24, impressions=900, clicks=21, add_to_cart=2)       # the day's last read
        t = self.sections()["tests"][0]
        self.assertTrue(t["live"])
        self.assertEqual((len(t["days"]), t["days"][0]["spend"], t["days"][0]["atc"], t["reads"]), (1, 24, 2, 2))
        self.assertEqual(t["totals"]["spend"], 24)             # the same day read twice counts once, in the CLI's figures too
        self.assertEqual(records.evaluate(self.conn, xid)["clicks"], 21)
        self.assertEqual((t["call"], t["sufficient"]), ("continue", False))   # the rule is shown; the data is still too thin to call
        self.assertIn("partial read", t["warnings"][0])
        self.assertIn("M-261021-76bc", t["rule_source"])

        from jarvis import sales_watch
        sales_watch.set_breakeven(1.4, "foot file")
        records.test_metric(self.conn, xid, "2", spend=50, impressions=2000, clicks=60, add_to_cart=3, checkouts=2)
        records.test_metric(self.conn, xid, "3", spend=50, impressions=2100, clicks=64, add_to_cart=4, checkouts=3, purchases=4, revenue=196)
        t = self.sections()["tests"][0]
        self.assertEqual([d["call"] for d in t["days"]], ["continue", "diagnose", "scale"])
        self.assertEqual((t["sufficient"], t["call"], t["roas_verdict"], t["breakeven"]), (True, "scale", "above", 1.4))
        self.assertEqual(t["days"][2]["cpa"], 12.5)
        records.test_metric(self.conn, xid, "2026-11-09", spend=124, clicks=145)   # a total filed under a date: flagged, not hidden
        self.assertTrue(any("two ways" in w for w in self.sections()["tests"][0]["warnings"]))

    def test_creative_reads_come_from_the_feedback_loop(self):
        slug = records.product_add(self.conn, "Foot File")
        records.test_add(self.conn, slug, "Cracked heels")
        self.run_cli("feedback", "add", "FF-03 BeforeAfter", "--kind", "ad", "--verdict", "good", "--source", "data",
                     "--what", "Static FF-03", "--why", "Top spender with 2 add-to-carts", "-p", slug, "--agent", "creative-director")
        ads = self.sections()["tests"][0]["ads"]
        self.assertEqual((len(ads), ads[0]["verdict"]), (1, "worked"))
        self.assertIn("FF-03", ads[0]["title"])

    # ------------------------------------------------------------ inbox

    def test_inbox_collects_what_waits_for_the_owner(self):
        self.assertEqual(self.sections()["inbox"], [])
        with unittest.mock.patch("jarvis.work._spawn", side_effect=fake_spawn):
            run = work.start("Research the foot file", task="T1", agent="product-research")
        run.update(status="stopped")
        work._save(run)
        jobs.add(self.conn, "Open Kalodata")                       # queued, and nobody is running the job runner
        for n in range(2):
            j = jobs.add(self.conn, f"Pull the ad library {n}")
            jobs.finish(self.conn, j, "The browser profile was locked.", ok=False)
        records.money_add(self.conn, "expense", "ads", 170, currency="AUD")   # past the A$160 kill line with no sale
        kinds = [i["kind"] for i in self.sections()["inbox"]]
        self.assertEqual(sorted(kinds), ["jobs", "kill", "runner", "worker"])   # several failed jobs are one line
        items = {i["kind"]: i for i in self.sections()["inbox"]}
        self.assertEqual(items["worker"]["id"], f"W{run['id']}")
        self.assertIn("kill rule", items["kill"]["title"])
        self.assertEqual(self.sections()["kpis"]["inbox"], 4)
        with unittest.mock.patch("jarvis.work._spawn", side_effect=fake_spawn):
            status, out = self.post("/api/control/worker", {"action": "resume", "id": f"W{run['id']}"})
        self.assertEqual((status, out["resumed"]), (200, [run["id"]]))
        self.assertNotIn("worker", [i["kind"] for i in self.sections()["inbox"]])

    # ------------------------------------------------------------ work: plans, worker models, debate mode

    def test_plan_view_of_a_parent_task(self):
        parent = records.task_add(self.conn, "Launch test: Foot File")
        a = records.task_add(self.conn, "Research it", parent=parent, agent="product-research")
        b = records.task_add(self.conn, "Draft the PDP", parent=parent, agent="shopify")
        records.task_update(self.conn, a, status="done", deliverable="brain/work/general/research.md", summary="Go, with one caution")
        records.task_update(self.conn, b, status="blocked", note="needs the supplier's photos")
        roster.set_task_model(b, "opus")
        plan = self.sections()["work"]["plans"][0]
        self.assertEqual((plan["id"], plan["subtasks"]["done"], plan["subtasks"]["total"], plan["progress"]), (f"T{parent}", 1, 2, 0.5))
        self.assertEqual([(s["id"], s["agent"], s["status"], s["deliverable"], s["model"]) for s in plan["steps"]],
                         [(f"T{a}", "product-research", "done", "brain/work/general/research.md", None),
                          (f"T{b}", "shopify", "blocked", None, "opus")])
        self.assertIn(f"T{b} blocked", plan["blockers"][0])
        # The record drawer and the deliverable preview behind a step.
        (self.home / "work" / "general").mkdir(parents=True, exist_ok=True)
        (self.home / "work" / "general" / "research.md").write_text("# Foot file\nGo.", encoding="utf-8")
        rec = json.loads(self.get(f"/api/record?id=T{parent}")[1])
        self.assertEqual((rec["kind"], [s["deliverable"] for s in rec["subtasks"]]), ("task", ["brain/work/general/research.md", None]))
        self.assertEqual(self.get("/api/file?path=brain/work/general/research.md")[1], b"# Foot file\nGo.")
        self.assertEqual(self.get("/api/file?path=secrets/dashboard_token")[0], 204)
        self.assertEqual(self.get("/api/record?id=T9999")[0], 404)
        self.assertEqual(self.post("/api/task-model", {"task": f"T{b}", "model": "claude-fable-5-2"})[0], 400)
        self.assertEqual(self.post("/api/task", {"title": "Check the checkout", "agent": "store-qa"})[0], 201)

    def test_each_worker_shows_its_model(self):
        with unittest.mock.patch("jarvis.work._spawn", side_effect=fake_spawn):
            status, run = self.post("/api/control/worker", {"action": "start", "brief": "Audit the ad account", "agent": "meta-ads-auditor",
                                                            "task": "T7", "model": "haiku"})
            self.assertEqual((status, run["model"]), (201, "haiku"))
            self.post("/api/control/worker", {"action": "start", "brief": "Write the daily overview"})
        ws = {w["agent"]: w for w in self.sections()["work"]["workers"]}
        self.assertEqual((ws["meta-ads-auditor"]["model"], ws["meta-ads-auditor"]["model_default"], ws["meta-ads-auditor"]["task"]), ("haiku", False, "T7"))
        self.assertEqual((ws["general-purpose"]["model"], ws["general-purpose"]["model_default"]), ("opus", True))
        self.assertEqual(self.sections()["work"]["worker_model"], "opus")
        self.assertEqual(self.sections()["live"]["count"], 2)

    def test_debate_mode_starts_a_council_worker_and_a_task(self):
        slug = records.product_add(self.conn, "Foot File")
        self.assertEqual(self.post("/api/debate", {"question": "kill?"})[0], 400)
        self.assertEqual(self.post("/api/debate", {"question": "Retest the foot file or drop it?", "product": "nothing"})[0], 400)
        with unittest.mock.patch("jarvis.work._spawn", side_effect=fake_spawn):
            status, out = self.post("/api/debate", {"question": "Retest the foot file or drop it?", "product": slug, "kind": "kill", "model": "sonnet"})
        self.assertEqual(status, 201)
        run = work.get(out["worker"])
        self.assertEqual((run["task"], run["model"]), (out["task"], "sonnet"))
        for needle in ("marketing-council", "bin/jarvis decide", f"--task {out['task']}", "--by jarvis", f"-p {slug}", out["deliverable"],
                       "approval request", "kill-or-continue"):
            self.assertIn(needle, run["brief"])
        task = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (int(out["task"][1:]),)).fetchone()
        self.assertEqual((task["status"], task["product"]), ("in_progress", slug))
        self.assertTrue(task["title"].startswith("Council debate: Retest"))

    # ------------------------------------------------------------ the reply path

    def test_reply_words_stream_to_the_page_before_the_voice(self):
        (self.home / "config.json").write_text(json.dumps({"claude_command": [*FAKE, "--fake-stream"]}), encoding="utf-8")
        q = self.server.hub.subscribe()
        self.post("/api/chat", {"text": "start the research"})
        events, deadline = [], time.time() + 15
        while time.time() < deadline:
            ev = q.get(timeout=15)
            events.append(ev)
            if ev == {"type": "status", "status": "idle"}:
                break
        kinds = [e["type"] for e in events]
        self.assertIn("say_delta", kinds)
        self.assertLess(kinds.index("say_delta"), kinds.index("say_chunk"))   # on the page before the first spoken sentence
        written = "".join(e["text"] for e in events if e["type"] == "say_delta")
        self.assertEqual(written, " ".join(e["text"] for e in events if e["type"] == "say_chunk"))

    def test_stopping_a_reply_leaves_claude_code_warm(self):
        runner = self.server.runner
        runner.warm()
        deadline = time.time() + 10
        while time.time() < deadline and not (runner.proc and runner.proc.poll() is None):
            time.sleep(0.05)
        first = runner.proc
        self.assertIsNotNone(first)
        self.assertEqual(self.post("/api/stop", {})[0], 200)
        deadline = time.time() + 10
        while time.time() < deadline and not (runner.proc and runner.proc is not first and runner.proc.poll() is None):
            time.sleep(0.05)
        self.assertIsNot(runner.proc, first)          # a new process is already up for the next message
        self.assertIsNone(runner.proc.poll())
        self.assertTrue(self.sections()["system"]["conversation"]["warm"])
        self.assertNotIn("[[", dashboard.VOICE_PROMPT)   # the stage's screen hints went with the stage


if __name__ == "__main__":
    unittest.main()
