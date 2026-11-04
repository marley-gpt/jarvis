import json
from datetime import datetime, timedelta

from helpers import BrainTest

from jarvis import db, pace, records


def iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


class PaceTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.now = datetime.now().astimezone()

    def test_behind_pace_speeds_up_ahead_slows_down(self):
        resets = self.now + timedelta(days=3, hours=12)  # half way through the week
        pace.record(self.conn, 10, iso(resets))
        s = pace.status(self.conn, self.now)
        self.assertEqual(s["status"], "behind pace: speed up")
        self.assertEqual(s["allowed_runs"], 4)  # capped by pace_max_batch
        self.assertFalse(s["estimate"])
        pace.record(self.conn, 60, iso(resets))
        s = pace.status(self.conn, self.now)
        self.assertEqual((s["status"], s["allowed_runs"]), ("ahead of pace: slow down", 0))

    def test_reserve_and_busy_five_hour_window_stop_background_work(self):
        resets = self.now + timedelta(hours=2)
        pace.record(self.conn, 91, iso(resets))
        self.assertEqual(pace.status(self.conn, self.now)["allowed_runs"], 0)
        self.assertIn("reserve", pace.status(self.conn, self.now)["status"])
        pace.record(self.conn, 5, iso(self.now + timedelta(days=6)), five_hour=90,
                    five_hour_resets=iso(self.now + timedelta(hours=1)))
        s = pace.status(self.conn, self.now)
        self.assertEqual(s["allowed_runs"], 0)
        self.assertIn("5-hour", s["status"])

    def test_runs_since_a_reading_are_estimated_and_recalibrate(self):
        resets = iso(self.now + timedelta(days=3))
        pace.record(self.conn, 10, resets)
        r = pace.reading(self.conn)
        r["ts"] = iso(self.now - timedelta(minutes=5))  # the runs come after the reading
        db.set_meta(self.conn, "usage", json.dumps(r))
        for i in range(4):
            records.log_agent_run(self.conn, "market-research", "s", "SubagentStart")
        s = pace.status(self.conn)
        self.assertTrue(s["estimate"])
        self.assertAlmostEqual(s["used_pct"], 12.0)  # 10 + 4 runs x 0.5
        pace.record(self.conn, 18, resets)  # 8 points over 4 runs: 2 each, blended 30% into 0.5
        self.assertAlmostEqual(pace.pct_per_run(self.conn), 0.95, places=2)

    def test_no_reading_is_a_labelled_estimate(self):
        s = pace.status(self.conn)
        self.assertTrue(s["estimate"])
        self.assertIn("ESTIMATE", s["source"])
        self.assertGreater(s["allowed_runs"], 0)
        out = self.run_cli("pace").stdout
        self.assertIn("ESTIMATE", out)
        self.run_cli("pace", "record", "--weekly", "3", "--resets", "2099-01-01T00:00:00Z", "--plan", "Pro")
        self.assertIn("Source: reading", self.run_cli("pace").stdout)
