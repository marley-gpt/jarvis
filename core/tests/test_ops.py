import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import BrainTest

from jarvis import ops, paths, projects, records, routines, sales_watch, social

NOW = datetime(2026, 11, 9, 14, 0).astimezone()
DAY = NOW.date().isoformat()


def quiet(row):
    return "test"


class SalesWatchTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()

    def order(self, oid, total=59.95, currency="AUD", day=DAY, product=None):
        return {"id": oid, "total": total, "currency": currency, "day": day, "product": product, "items": "1x Brush"}

    def test_orders_are_recorded_once_and_announced(self):
        rep = sales_watch.process(self.conn, [self.order("1")], None, now=NOW, notify=quiet)
        self.assertEqual(rep["new_orders"], 1)
        self.assertEqual([a["kind"] for a in rep["alerts"]], ["first_sale"])
        self.assertIn("First sale of the day", rep["alerts"][0]["say"])
        rep = sales_watch.process(self.conn, [self.order("1"), self.order("2", 40)], None, now=NOW, notify=quiet)
        self.assertEqual(rep["new_orders"], 1)
        self.assertEqual([a["kind"] for a in rep["alerts"]], ["sale"])
        self.assertIn("2 today", rep["alerts"][0]["say"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM money WHERE kind='income'").fetchone()[0], 2)
        self.assertEqual(rep["figures"]["sales_aud"], 99.95)
        # The spoken feed and the activity log both carry it, for the dashboard and the call.
        self.assertEqual([a["kind"] for a in sales_watch.announcements()], ["first_sale", "sale"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_events WHERE agent='accountant'").fetchone()[0], 2)

    def test_usd_orders_are_converted(self):
        sales_watch.process(self.conn, [self.order("9", 50, "USD")], None, now=NOW, notify=quiet)
        aud = self.conn.execute("SELECT amount_aud FROM money").fetchone()[0]
        self.assertEqual(aud, round(50 * paths.config()["usd_aud"], 2))

    def test_kill_rule_is_urgent_after_two_dry_days(self):
        yesterday = (NOW - timedelta(days=1)).date().isoformat()
        records.money_add(self.conn, "expense", "ads", 85, day=yesterday, ref="meta:x:" + yesterday)
        rep = sales_watch.process(self.conn, [], {"day": DAY, "spend": 80, "currency": "AUD", "add_to_cart": 2,
                                                  "account": "x"}, now=NOW, notify=quiet)
        kinds = {a["kind"]: a for a in rep["alerts"]}
        self.assertIn("kill", kinds)
        self.assertTrue(kinds["kill"]["urgent"])
        self.assertEqual(rep["figures"]["days_without_sale"], 2)
        # Said once a day, not every 15 minutes.
        again = sales_watch.process(self.conn, [], {"day": DAY, "spend": 90, "currency": "AUD", "account": "x"},
                                    now=NOW, notify=quiet)
        self.assertNotIn("kill", [a["kind"] for a in again["alerts"]])
        # Spend is one ledger line per account and day, updated as it grows.
        self.assertEqual(self.conn.execute("SELECT amount FROM money WHERE ref = ?", (f"meta:x:{DAY}",)).fetchone()[0], 90)

    def test_a_sale_resets_the_kill_clock(self):
        yesterday = (NOW - timedelta(days=1)).date().isoformat()
        records.money_add(self.conn, "expense", "ads", 150, day=yesterday)
        records.money_add(self.conn, "income", "sales", 60, day=yesterday)
        rep = sales_watch.process(self.conn, [], {"day": DAY, "spend": 30, "currency": "AUD"}, now=NOW, notify=quiet)
        self.assertEqual(rep["figures"]["spend_since_sale_aud"], 30)
        self.assertNotIn("kill", [a["kind"] for a in rep["alerts"]])

    def test_day_budget_and_zero_atc_cite_blakes_rules(self):
        rep = sales_watch.process(self.conn, [], {"day": DAY, "spend": 80, "currency": "AUD", "add_to_cart": 0,
                                                  "checkouts": 0}, now=NOW, notify=quiet)
        kinds = {a["kind"]: a for a in rep["alerts"]}
        self.assertIn("no_sales", kinds)
        self.assertIn("no_intent", kinds)
        self.assertIn("Blake", kinds["no_intent"]["text"])
        self.assertFalse(kinds["no_sales"]["urgent"])

    def test_evening_summary_against_the_weekly_target(self):
        sales_watch.process(self.conn, [self.order("5", 100)], None, now=NOW, notify=quiet)
        rep = sales_watch.process(self.conn, [], None, now=NOW.replace(hour=21, minute=5), notify=quiet)
        summary = [a for a in rep["alerts"] if a["kind"] == "summary"]
        self.assertEqual(len(summary), 1)
        self.assertIn("A$380", summary[0]["text"])

    def test_nothing_happening_says_nothing(self):
        rep = sales_watch.process(self.conn, [], None, now=NOW.replace(hour=22), notify=quiet)
        self.assertEqual(rep["alerts"], [])

    def test_ingest_validates(self):
        with self.assertRaises(sales_watch.SalesError):
            sales_watch.ingest(self.conn, {"orders": [{"id": "1"}]}, notify=quiet)
        with self.assertRaises(sales_watch.SalesError):
            sales_watch.ingest(self.conn, {"meta": {"spend": 3}}, notify=quiet)
        rep = sales_watch.ingest(self.conn, {"orders": [{"id": "7", "total": 30, "currency": "AUD"}]}, notify=quiet)
        self.assertEqual(rep["new_orders"], 1)

    def test_fetchers_parse_the_apis(self):
        (self.home / "secrets").mkdir(exist_ok=True)
        (self.home / "secrets" / "shopify.json").write_text(json.dumps({"shop": "demo", "token": "t"}))
        (self.home / "secrets" / "meta.json").write_text(json.dumps({"token": "m", "ad_account": "123"}))
        calls = []

        def fake(url, data=None, headers=None, timeout=30):
            calls.append((url, headers))
            if "myshopify" in url:
                node = {"id": "gid://shopify/Order/42", "name": "#1001", "createdAt": "2026-11-09T03:00:00Z",
                        "test": False, "cancelledAt": None, "displayFinancialStatus": "PAID",
                        "currentTotalPriceSet": {"shopMoney": {"amount": "49.95", "currencyCode": "USD"}},
                        "lineItems": {"nodes": [{"title": "Brush", "quantity": 1, "product": {"handle": "brush"}}]}}
                test = dict(node, id="gid://shopify/Order/43", test=True)
                return {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node, test]}}}
            return {"data": [{"spend": "12.5", "account_currency": "AUD",
                              "actions": [{"action_type": "add_to_cart", "value": "3"}]}]}

        orders = sales_watch.fetch_orders(NOW, http=fake)
        self.assertEqual([o["id"] for o in orders], ["42"])
        self.assertEqual(orders[0]["total"], 49.95)
        self.assertEqual(calls[0][1]["X-Shopify-Access-Token"], "t")
        self.assertIn("demo.myshopify.com", calls[0][0])
        m = sales_watch.fetch_meta(DAY, http=fake)
        self.assertEqual((m["spend"], m["add_to_cart"], m["account"]), (12.5, 3, "act_123"))
        rep = sales_watch.watch(self.conn, now=NOW, http=fake, notify=quiet)
        self.assertEqual(rep["new_orders"], 1)
        self.assertTrue(rep["shopify"] and rep["meta"])

    def test_watch_without_sources_or_with_errors_still_runs(self):
        paths.set_config("sales_feed", "off")
        rep = sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertEqual((rep["errors"], rep["source"], rep["live_meta"]), ([], "off", False))
        paths.set_config("sales_feed", "tokens")
        (self.home / "secrets").mkdir(exist_ok=True)
        (self.home / "secrets" / "shopify.json").write_text(json.dumps({"shop": "demo", "token": "t"}))

        def broken(*a, **k):
            raise sales_watch.SalesError("demo.myshopify.com: timed out")

        rep = sales_watch.watch(self.conn, now=NOW, http=broken, notify=quiet)
        self.assertEqual(len(rep["errors"]), 1)
        self.assertFalse(rep["live_shopify"])


class SalesConnectorsTest(BrainTest):
    """T103: the live feed through the claude.ai connectors, and the ROAS tile's figures."""

    def setUp(self):
        super().setUp()
        self.init()
        os.environ.pop("FAKE_FEED_MODE", None)

    def tearDown(self):
        os.environ.pop("FAKE_FEED_MODE", None)
        super().tearDown()

    def ads(self, day):
        return self.conn.execute("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='expense' AND category='ads' "
                                 "AND day = ?", (day,)).fetchone()[0]

    def test_auto_uses_the_connectors_until_both_tokens_are_in(self):
        self.assertEqual(sales_watch.feed_mode(), "connectors")
        (self.home / "secrets").mkdir(exist_ok=True)
        (self.home / "secrets" / "shopify.json").write_text(json.dumps({"shop": "demo", "token": "t"}))
        self.assertEqual(sales_watch.feed_mode(), "connectors")
        (self.home / "secrets" / "meta.json").write_text(json.dumps({"token": "m", "ad_account": "1"}))
        self.assertEqual(sales_watch.feed_mode(), "tokens")

    def test_the_pull_is_read_only_and_outside_the_repo(self):
        seen = {}

        def run(prompt, timeout):
            seen["prompt"] = prompt
            return ""

        sales_watch.fetch_connectors(NOW, run=run)
        self.assertIn('"time_increment": "1"', seen["prompt"])
        self.assertIn("2023827758161105", seen["prompt"])
        self.assertIn("created_at:>=", seen["prompt"])
        self.assertNotIn("mutation", seen["prompt"])
        with mock.patch("subprocess.run") as sp:
            sp.return_value = mock.Mock(stdout='{"type":"result"}', stderr="")
            with mock.patch.dict(os.environ, {"JARVIS_SALES_FEED_COMMAND": ""}), \
                    mock.patch("jarvis.dashboard.find_claude", return_value=["claude"]):
                sales_watch._claude_feed("x", 5)
        cmd, kw = sp.call_args[0][0], sp.call_args[1]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "ToolSearch")
        self.assertEqual(set(cmd[cmd.index("--allowedTools") + 1].split(",")), {"ToolSearch", *sales_watch.FEED_TOOLS})
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
        self.assertNotEqual(Path(kw["cwd"]).resolve(), paths.REPO_ROOT.resolve())

    def test_connector_feed_records_orders_and_spend(self):
        rep = sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertEqual((rep["source"], rep["errors"], rep["new_orders"]), ("connectors", [], 1))
        self.assertTrue(rep["live_meta"] and rep["live_shopify"])
        self.assertEqual(self.ads(DAY), 20)
        self.assertEqual(self.ads((NOW - timedelta(days=1)).date().isoformat()), 10)
        # A second pull doubles nothing.
        rep = sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertEqual(rep["new_orders"], 0)
        self.assertEqual(self.ads(DAY), 20)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM money WHERE kind='income'").fetchone()[0], 1)

    def test_hand_written_lines_are_topped_up_not_doubled(self):
        yesterday = (NOW - timedelta(days=1)).date().isoformat()
        records.money_add(self.conn, "expense", "ads", 8, day=yesterday, source="Meta read by hand", ref="meta:" + yesterday)
        records.money_add(self.conn, "income", "sales", 50, day=DAY, ref="shopify:1777")
        sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertEqual(self.ads(yesterday), 10)  # Meta says A$10: the feed adds the A$2 missing
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM money WHERE kind='income'").fetchone()[0], 1)
        # A hand line above Meta's figure is left alone; the feed adds nothing.
        records.money_add(self.conn, "expense", "ads", 30, day=DAY, ref="manual:" + DAY)
        sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertEqual(self.ads(DAY), 30)

    def test_a_stray_tool_voids_the_pull(self):
        os.environ["FAKE_FEED_MODE"] = "stray"
        rep = sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertIn("may not", rep["errors"][0])
        self.assertEqual((rep["new_orders"], self.ads(DAY)), (0, 0))
        self.assertFalse(rep["live_meta"] or rep["live_shopify"])

    def test_roas_tile_figures_and_live_flag(self):
        r = sales_watch.roas(self.conn, now=NOW)
        self.assertFalse(r["live"])
        self.assertIn("hasn't pulled", r["why_not_live"])
        self.assertIsNone(r["today"]["roas"])
        sales_watch.watch(self.conn, now=NOW, notify=quiet)
        r = sales_watch.roas(self.conn, day=DAY)  # the pull just happened (real clock), so it is live
        self.assertTrue(r["live"], r["why_not_live"])
        self.assertEqual(r["day"], DAY)
        self.assertEqual((r["today"]["revenue_aud"], r["today"]["spend_aud"]), (50, 20))
        self.assertEqual(r["today"]["roas"], 2.5)
        self.assertEqual((r["week"]["revenue_aud"], r["week"]["spend_aud"], r["week"]["roas"]), (50, 30, 1.67))
        self.assertIsNone(r["breakeven"])
        self.assertIsNone(r["today"]["verdict"])
        sales_watch.set_breakeven(2.0, "test product")
        r = sales_watch.roas(self.conn, day=DAY)
        self.assertEqual((r["breakeven"], r["today"]["verdict"], r["week"]["verdict"]), (2.0, "above", "below"))
        with self.assertRaises(sales_watch.SalesError):
            sales_watch.set_breakeven(0)
        # An hour later with no new pull, it is no longer live.
        r = sales_watch.roas(self.conn, day=DAY, now=datetime.now().astimezone() + timedelta(hours=1))
        self.assertFalse(r["live"])
        self.assertIn("min ago", r["why_not_live"])

    def test_a_failed_source_is_not_live(self):
        os.environ["FAKE_FEED_MODE"] = "shopify_error"
        rep = sales_watch.watch(self.conn, now=NOW, notify=quiet)
        self.assertTrue(rep["live_meta"])
        self.assertFalse(rep["live_shopify"])
        self.assertIn("Access denied", rep["errors"][0])
        r = sales_watch.roas(self.conn, now=datetime.now().astimezone())
        self.assertFalse(r["live"])
        self.assertIn("Shopify", r["why_not_live"])

    def test_cli_and_endpoint(self):
        out = self.run_cli("sales", "breakeven", "1.41", "--note", "brush 2-pack").stdout
        self.assertIn("1.41x", out)
        self.assertIn("NO LIVE DATA", self.run_cli("sales", "roas").stdout)
        self.run_cli("sales", "watch", "--quiet")
        out = self.run_cli("sales", "roas").stdout
        self.assertIn("[live]", out)
        self.assertIn("breakeven 1.41x", out)
        status, data = ops.handle_get("/api/ops/roas")
        self.assertEqual(status, 200)
        self.assertTrue(data["live"])
        self.assertEqual(data["source_label"], "Shopify + Meta connectors")


class SocialTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        records.product_add(self.conn, "Lymphatic Face Brush")
        records.product_add(self.conn, "Just An Idea")
        records.product_update(self.conn, "lymphatic-face-brush", stage="testing")

    def test_plan_fills_the_calendar_once(self):
        added = social.plan(self.conn, days=3, platforms=["instagram", "facebook"], start=date(2026, 11, 10))
        self.assertEqual(len(added), 6)
        self.assertEqual({a["product"] for a in added}, {"lymphatic-face-brush"})
        self.assertEqual(len({a["format"] for a in added}), 6)
        self.assertEqual(social.plan(self.conn, days=3, platforms=["instagram", "facebook"], start=date(2026, 11, 10)), [])
        with self.assertRaises(social.SocialError):
            social.plan(self.conn, days=1, platforms=["myspace"], start=date(2026, 12, 1))

    def test_dishonest_drafts_are_held_and_cannot_be_submitted(self):
        s = social.plan(self.conn, days=1, platforms=["instagram"], start=date(2026, 11, 10))[0]
        item = social.draft(s["id"], "Dermatologists love it. 10,000+ reviews!")
        self.assertEqual(item["status"], "needs-review")
        self.assertIn("dermatologists", item["flags"])
        with self.assertRaises(social.SocialError):
            social.submit(self.conn, s["id"])

    def test_nothing_goes_out_without_approval(self):
        s = social.plan(self.conn, days=1, platforms=["instagram"], start=date(2026, 11, 10))[0]
        social.draft(s["id"], "Puffy mornings? Two minutes with the brush.", "#skincare")
        item, aid = social.submit(self.conn, s["id"], task=None)
        row = self.conn.execute("SELECT kind, status FROM approvals WHERE id = ?", (aid,)).fetchone()
        self.assertEqual((row["kind"], row["status"]), ("other", "pending"))
        with self.assertRaises(social.SocialError):
            social.posted(self.conn, s["id"])
        records.approval_decide(self.conn, aid, True)
        self.assertEqual(social.posted(self.conn, s["id"], "https://instagram.com/p/x")["status"], "posted")

    def test_comment_replies_and_brief(self):
        r = social.comment("instagram", "Does it work on sensitive skin?", author="sam")
        self.assertEqual(r["id"], "R1")
        social.plan(self.conn, days=1, platforms=["facebook"], start=date(2026, 11, 10))
        path = social.brief(self.conn)
        text = path.read_text(encoding="utf-8")
        self.assertIn("R1", text)
        self.assertIn("D11", text)
        self.assertEqual(social.reply("R1", "Soft bristles, but patch test first.")["status"], "drafted")
        _, aid = social.submit(self.conn, "R1")
        records.approval_decide(self.conn, aid, False)
        self.assertEqual(social.sync(self.conn)[0]["status"], "rejected")
        self.assertEqual(social.board()["replies"]["rejected"], 1)


class ProjectsTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()

    def test_overview_rolls_up_subtasks_workers_deadlines_and_blockers(self):
        top = records.task_add(self.conn, "Launch the brush test by 2026-11-12")
        a = records.task_add(self.conn, "research", parent=top, agent="product-research")
        b = records.task_add(self.conn, "creative", parent=top, agent="creative-director")
        records.task_update(self.conn, a, status="done")
        records.task_update(self.conn, b, status="blocked", note="waiting on photos")
        aid = records.approval_request(self.conn, "spend", "Buy samples", task=b)
        quiet_top = records.task_add(self.conn, "Something calm")
        ov = projects.overview(self.conn, today=date(2026, 11, 10),
                               workers=[{"id": 3, "task": f"T{b}", "status": "stopped"}])
        p = next(x for x in ov["projects"] if x["id"] == f"T{top}")
        self.assertEqual(ov["projects"][0]["id"], f"T{top}")  # the one with a deadline comes first
        self.assertEqual(p["subtasks"], {"total": 2, "done": 1, "open": 1})
        self.assertEqual((p["deadline"], p["days_left"]), ("2026-11-12", 2))
        self.assertEqual(p["approvals"], [f"A{aid}"])
        self.assertTrue(any("W3 stopped" in x for x in p["blockers"]))
        self.assertTrue(any(f"T{b} waiting approval" in x for x in p["blockers"]))  # the approval moved it on from blocked
        self.assertEqual(ov["counts"]["due_7_days"], 1)
        projects.set_deadline(self.conn, f"T{quiet_top}", "2026-11-01")
        ov = projects.overview(self.conn, today=date(2026, 11, 10), workers=[])
        q = next(x for x in ov["projects"] if x["id"] == f"T{quiet_top}")
        self.assertIn("overdue by 9 day(s)", q["blockers"])
        self.assertTrue(projects.lines(ov)[0].startswith("Projects (2026-11-10)"))
        with self.assertRaises(records.RecordError):
            projects.set_deadline(self.conn, f"T{quiet_top}", "next week")


class RoutinesTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        self.sched = self.home / "scheduled"
        for name in ("jarvis-morning-brief", "jarvis-evening-summary", "jarvis-daily-learning", "jarvis-skill-learning"):
            (self.sched / name).mkdir(parents=True)
            (self.sched / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n\nDo the {name} job.", encoding="utf-8")
        self.patch = mock.patch.object(routines, "SCHEDULED", self.sched)
        self.patch.start()
        self.started = []

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def worker(self, brief):
        self.started.append(brief)
        return {"id": 40 + len(self.started), "session": "s"}

    def test_local_routines_run_on_their_interval(self):
        at = datetime(2026, 11, 9, 3, 0)
        done = routines.tick(self.conn, now=at, start_worker=self.worker)
        names = [e["routine"] for e in done]
        self.assertEqual(names[:3], ["sales-watch", "worker-resume", "social-sync"])
        self.assertTrue(all(e["ok"] for e in done), done)
        again = routines.tick(self.conn, now=at + timedelta(minutes=15), start_worker=self.worker)
        self.assertEqual([e["routine"] for e in again], ["sales-watch", "phone-line", "reels-retry"])

    def test_missed_morning_brief_starts_a_worker_once(self):
        at = datetime(2026, 11, 9, 8, 50)
        done = routines.tick(self.conn, now=at, start_worker=self.worker)
        self.assertIn("morning-brief", [e["routine"] for e in done])
        self.assertEqual(len(self.started), 1)
        self.assertIn("Do the jarvis-morning-brief job", self.started[0])
        self.assertIn("routines mark morning-brief", self.started[0])
        routines.tick(self.conn, now=at + timedelta(minutes=15), start_worker=self.worker)
        self.assertEqual(len(self.started), 1)

    def test_a_written_report_or_mark_means_done(self):
        (self.home / "reports").mkdir(exist_ok=True)
        (self.home / "reports" / "2026-11-09-morning.md").write_text("brief", encoding="utf-8")
        routines.tick(self.conn, now=datetime(2026, 11, 9, 8, 50), start_worker=self.worker)
        self.assertEqual(self.started, [])
        routines.mark("evening-summary", now=datetime(2026, 11, 9, 21, 30))
        routines.tick(self.conn, now=datetime(2026, 11, 9, 21, 50), start_worker=self.worker)
        self.assertEqual(self.started, [])

    def test_too_late_is_skipped_and_learning_is_paced(self):
        routines.tick(self.conn, now=datetime(2026, 11, 9, 15, 0), start_worker=self.worker)
        self.assertEqual(self.started, [])
        routines.mark("evening-summary", now=datetime(2026, 11, 9, 21, 30))
        with mock.patch("jarvis.pace.status", return_value={"allowed_runs": 0, "status": "reserve reached"}):
            done = routines.tick(self.conn, now=datetime(2026, 11, 9, 23, 5), start_worker=self.worker)
        learning = next(e for e in done if e["routine"] == "nightly-learning")
        self.assertIn("paused", learning["note"])
        self.assertEqual(self.started, [])

    def test_status_line_and_overrides(self):
        self.assertTrue(routines.status_line(routines.status(check_scheduler=False)).startswith("Routines off"))
        routines.tick(self.conn, now=datetime.now(), start_worker=self.worker)
        s = routines.status(check_scheduler=False)
        self.assertTrue(s["on"])
        self.assertIn("Routines on", routines.status_line(s))
        cfg = json.loads((self.home / "config.json").read_text()) if (self.home / "config.json").exists() else {}
        cfg["routines"] = {"social-sync": {"enabled": False}}
        (self.home / "config.json").write_text(json.dumps(cfg))
        self.assertFalse(routines.get("social-sync")["enabled"])
        with self.assertRaises(routines.RoutineError):
            routines.mark("nope")

    def test_overlapping_ticks_are_skipped(self):
        (routines.folder() / "tick.lock").write_text("1")
        self.assertEqual(routines.tick(self.conn, now=datetime(2026, 11, 9, 3), start_worker=self.worker), [])


class OpsSurfaceTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()

    def test_dashboard_routes(self):
        for name in ("projects", "sales", "social", "routines", "overview"):
            code, data = ops.handle_get(f"/api/ops/{name}")
            self.assertEqual(code, 200)
            json.dumps(data, default=str)
        self.assertIsNone(ops.handle_get("/api/ops/nope"))

    def test_cli(self):
        self.assertIn("projects", json.loads(self.run_cli("projects", "--json").stdout))
        self.assertIn("routines", json.loads(self.run_cli("routines", "--json").stdout))
        self.assertIn("Sales today", self.run_cli("sales").stdout)
        self.assertIn("read_orders", self.run_cli("sales", "setup").stdout)
        self.assertIn("empty", self.run_cli("social").stdout)
        self.run_cli("product", "add", "Brush")
        self.assertIn("slot(s) added", self.run_cli("social", "plan", "--days", "2").stdout)
        self.assertIn("needs review", self.run_cli("social", "draft", "S1", "--caption", "Clinically proven!").stdout)
        self.assertNotEqual(self.run_cli("social", "submit", "S1", check=False).returncode, 0)
        out = self.run_cli("sales", "ingest", stdin=json.dumps({"orders": [{"id": "1", "total": 20}]})).stdout
        self.assertIn("1 new order", out)
