import sqlite3
import zipfile

from helpers import BrainTest

from jarvis import backup, memory, records


class RecordsTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.slug = records.product_add(self.conn, "Posture Corrector")

    def test_task_tree_and_history(self):
        parent = records.task_add(self.conn, "Launch prep", product=self.slug)
        child = records.task_add(self.conn, "Research", agent="product-research", product=self.slug,
                                 parent=f"T{parent}")
        records.task_update(self.conn, f"T{child}", status="in_progress", note="started")
        _, _, children = records.task_get(self.conn, f"T{parent}")
        self.assertEqual([c["id"] for c in children], [child])
        _, events, _ = records.task_get(self.conn, child)
        self.assertEqual([e["kind"] for e in events], ["created", "status:in_progress"])
        open_ids = [t["id"] for t in records.task_list(self.conn, open_only=True)]
        self.assertEqual(sorted(open_ids), [parent, child])

    def test_task_requires_known_product(self):
        with self.assertRaises(records.RecordError):
            records.task_add(self.conn, "x", product="nope")

    def test_approval_flow_marks_task(self):
        tid = records.task_add(self.conn, "Launch", product=self.slug)
        aid = records.approval_request(self.conn, "spend", "Run $50 test", task=tid, amount=50, currency="USD")
        self.assertEqual(records.task_get(self.conn, tid)[0]["status"], "waiting_approval")
        row = records.approval_decide(self.conn, f"A{aid}", True, "go")
        self.assertEqual(row["status"], "approved")
        with self.assertRaises(records.RecordError):
            records.approval_decide(self.conn, aid, False)
        with self.assertRaises(records.RecordError):
            records.approval_request(self.conn, "yolo", "x")

    def test_test_needs_data_before_pass(self):
        xid = records.test_add(self.conn, self.slug, "Back pain angle converts", criteria="CPA < $25")
        records.test_metric(self.conn, xid, "2026-10-20", spend=12, impressions=1500, clicks=20, purchases=1,
                            revenue=39)
        with self.assertRaises(records.RecordError):
            records.test_update(self.conn, xid, status="passed")
        records.test_metric(self.conn, xid, "2026-10-21", spend=30, impressions=4000, clicks=60, add_to_cart=6,
                            purchases=2, revenue=78)
        ev = records.evaluate(self.conn, xid)
        self.assertTrue(ev["sufficient"])
        self.assertAlmostEqual(ev["cpa"], 14.0)
        self.assertAlmostEqual(ev["ctr"], 80 / 5500, places=4)
        records.test_update(self.conn, xid, status="passed", verdict="CPA $14 under $25 target")
        self.assertEqual(self.conn.execute("SELECT status FROM tests").fetchone()[0], "passed")

    def test_default_test_budget_from_config(self):
        xid = records.test_add(self.conn, self.slug, "h")
        row = self.conn.execute("SELECT budget_usd, planned_days FROM tests WHERE id = ?", (xid,)).fetchone()
        self.assertEqual((row[0], row[1]), (50.0, 3))

    def test_cost_budget(self):
        records.cost_log(self.conn, "openai-images", 45)
        self.assertTrue(records.cost_check(self.conn, 4)[0])
        self.assertFalse(records.cost_check(self.conn, 6)[0])
        (self.home / "config.json").write_text('{"monthly_ai_budget_aud": 100}', encoding="utf-8")
        self.assertTrue(records.cost_check(self.conn, 6)[0])

    def test_backup_contains_db_and_memory_and_prunes(self):
        self.init()
        (self.home / "config.json").write_text('{"backup_keep": 2}', encoding="utf-8")
        made = [backup.create(self.conn, f"t{i}") for i in range(3)]
        self.assertFalse(made[0].exists())
        with zipfile.ZipFile(made[-1]) as zf:
            names = zf.namelist()
        self.assertIn("jarvis.db", names)
        self.assertTrue(any(n.startswith("memory/business/") for n in names))
        self.assertIsNone(backup.daily(self.conn))  # already backed up today

    def test_backup_restores_records(self):
        records.task_add(self.conn, "Remember me")
        path = backup.create(self.conn)
        with zipfile.ZipFile(path) as zf:
            zf.extract("jarvis.db", self.home / "restore")
        restored = sqlite3.connect(self.home / "restore" / "jarvis.db")
        self.assertEqual(restored.execute("SELECT title FROM tasks").fetchone()[0], "Remember me")
        restored.close()

    def test_workpath_is_per_product(self):
        tid = records.task_add(self.conn, "Customer research!", product=self.slug)
        path = records.task_workpath(self.conn, tid)
        self.assertEqual(path.parent.name, self.slug)
        self.assertEqual(path.name, f"T{tid}-customer-research.md")
        self.assertTrue(path.parent.is_dir())
        self.assertEqual(memory.slugify("Ä b/c"), "b-c")


class FeedbackTest(BrainTest):
    def test_feedback_is_recorded_listed_and_digested(self):
        from jarvis import feedback
        self.init()
        feedback.add(self.conn, about="BOF static 4 for posture corrector", kind="ad", verdict="good", source="data",
                     what="problem-first headline, 2 floating units", why="clear pain hook", keep="problem-first headlines",
                     change="", evidence="CTR 3.1%", product=None, agent="creative-director", task="T9")
        feedback.add(self.conn, about="BOF static 7", kind="ad", verdict="bad", source="owner", what="busy layout",
                     why="too many callouts", change="max 3 callouts", agent="creative-director")
        rows = feedback.rows(self.conn, agent="creative-director")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(feedback.rows(self.conn, verdict="bad")), 1)
        d = feedback.digest(self.conn, agent="creative-director")
        self.assertIn("problem-first headlines", d)
        self.assertIn("max 3 callouts", d)
        self.assertIn("WORKED", feedback.ledger_path().read_text(encoding="utf-8"))
        with self.assertRaises(feedback.FeedbackError):
            feedback.add(self.conn, about="x", kind="nope", verdict="good", source="owner", what="a", why="b")


class MoneyTest(BrainTest):
    """The accountant's ledger: costs, sales, profit and loss for a period and for a product test."""

    def setUp(self):
        super().setUp()
        self.slug = records.product_add(self.conn, "Posture Corrector")

    def test_report_totals_against_the_weekly_target(self):
        records.money_add(self.conn, "income", "sales", 300, day="2026-11-02")
        records.money_add(self.conn, "expense", "ads", 160, day="2026-11-02", ref="meta:2026-11-02")
        records.money_add(self.conn, "expense", "subscriptions", 20, currency="USD", day="2026-11-03", note="Higgsfield")
        records.cost_log(self.conn, "claude-api", 5)  # AI usage logged today, outside the period
        rep = records.money_report(self.conn, "2026-11-02", "2026-11-08")
        self.assertEqual(rep["total_income"], 300)
        self.assertEqual(rep["expenses"]["subscriptions"], round(20 * 1.52, 2))  # USD converted
        self.assertEqual(rep["profit"], round(300 - 160 - 30.4, 2))
        self.assertEqual(rep["target"], 380)
        self.assertNotIn("ai_api", rep["expenses"])

    def test_same_ref_updates_instead_of_double_counting(self):
        records.money_add(self.conn, "expense", "ads", 40, day="2026-11-02", ref="meta:2026-11-02")
        records.money_add(self.conn, "expense", "ads", 95, day="2026-11-02", ref="meta:2026-11-02")  # day finished
        rep = records.money_report(self.conn, "2026-11-02", "2026-11-02")
        self.assertEqual(rep["expenses"], {"ads": 95})

    def test_unknown_category_and_currency_are_refused(self):
        with self.assertRaises(records.RecordError):
            records.money_add(self.conn, "expense", "lunch", 10)
        with self.assertRaises(records.RecordError):
            records.money_add(self.conn, "expense", "ads", 10, currency="EUR")

    def test_product_test_profit_and_loss(self):
        tid = records.test_add(self.conn, self.slug, "Neck pain angle beats posture angle")
        records.test_metric(self.conn, tid, "2026-11-02", spend=75, clicks=60, purchases=3, revenue=150)
        records.money_add(self.conn, "expense", "cogs", 24, test=f"X{tid}", product=self.slug)
        records.money_add(self.conn, "expense", "ads", 75, test=f"X{tid}")  # already in the metrics, not counted twice
        p = records.test_pnl(self.conn, f"X{tid}")
        self.assertEqual(p["ad_spend"], 75)
        self.assertEqual(p["other_costs"], {"cogs": 24})
        self.assertEqual(p["profit"], 150 - 75 - 24)
