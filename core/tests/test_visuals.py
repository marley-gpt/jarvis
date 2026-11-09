import json

from helpers import BrainTest

from jarvis import records, visuals


class VisualsTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()

    def kinds(self, text):
        return [c["kind"] for c in visuals.cards(self.conn, text)]

    def test_counts_money_and_percent(self):
        cs = visuals.cards(self.conn, "We have 412 applications and spent A$380 this week, a 3.2% conversion rate.")
        by = {c["kind"]: c for c in cs}
        self.assertEqual(by["count"]["value"], 412)
        self.assertEqual(by["count"]["label"], "applications")
        self.assertEqual(by["money"]["value"], 380)
        self.assertEqual(by["percent"]["value"], 3.2)
        self.assertEqual(self.kinds("I took 3 days over it."), [])

    def test_ids_resolve_through_the_records(self):
        tid = records.task_add(self.conn, "Draft the PDP", agent="shopify")
        aid = records.approval_request(self.conn, "spend", "Test budget", task=tid, amount=40, currency="USD")
        cs = visuals.cards(self.conn, f"T{tid} is done and A{aid} needs you; T999 does not exist.")
        self.assertEqual([c["kind"] for c in cs], ["task", "approval"])
        self.assertEqual(cs[0]["title"], "Draft the PDP")
        self.assertEqual(cs[1]["status"], "pending")

    def test_lists_dates_products_files(self):
        records.product_add(self.conn, "Lint Brush")
        cs = visuals.cards(self.conn, "The shortlist is Ava, Marcus and Priya.")
        self.assertEqual(cs[0]["items"], ["Ava", "Marcus", "Priya"])
        self.assertIn("date", self.kinds("The supplier call is booked for Thursday at 3pm."))
        self.assertIn("product", self.kinds("The lint brush page is ready."))
        self.assertEqual(visuals.cards(self.conn, "See brain/work/x/ad.png")[0]["kind"], "image")

    def test_markers_win_and_are_stripped(self):
        text = "Here it is. [[count 412 applications]] [[list a; b; c]]"
        cs = visuals.cards(self.conn, text)
        self.assertEqual([c["kind"] for c in cs], ["count", "list"])
        self.assertEqual(visuals.strip_markers(text), "Here it is.")
        self.assertEqual(visuals.cards(self.conn, "[[file /etc/passwd]]"), [])
        tid = records.task_add(self.conn, "Draft the PDP", agent="shopify")
        cs = visuals.cards(self.conn, f"T{tid} has 40 images and spent $12. [[count 3 ads]]")
        self.assertEqual([c["kind"] for c in cs], ["count", "task"])  # ids still resolve; guesses give way to the hint

    def test_a_case_for_taking_a_job_on(self):
        c = visuals.cards(self.conn, "Let me run lead generation. [[case I take lead generation; it is daily routine; it frees you for calls]]")[0]
        self.assertEqual((c["kind"], c["title"], len(c["items"])), ("case", "I take lead generation", 2))
