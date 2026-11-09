import unittest

from jarvis import unit_economics as ue


class UnitEconomicsTest(unittest.TestCase):
    def test_matches_memory_brush_figures(self):
        c = ue.calc("brush", 43, 11, usd_aud=1.52, weekly_target_aud=380)
        self.assertEqual(c["gross_profit"], 30.45)
        self.assertAlmostEqual(c["contribution_margin"], 0.7081, places=3)
        self.assertEqual(c["break_even_roas"], 1.41)
        self.assertEqual(c["flags"]["gross_margin"], "green")

    def test_x1_is_red(self):
        c = ue.calc("x1", 43, 11, spend_aud=146, orders=1, usd_aud=1.52, weekly_target_aud=380)
        self.assertEqual(c["cac_usd"], 96.05)
        self.assertEqual(c["roas"], 0.45)
        self.assertEqual(c["net_profit_usd"], -65.6)
        self.assertEqual(c["gap_to_target_aud"], -479.71)
        reds = {k: v for k, v in c["flags"].items() if k != "gross_margin"}
        self.assertEqual(set(reds.values()), {"red"})
        self.assertFalse(c["sufficient"])

    def test_no_ad_data_gives_no_verdict(self):
        c = ue.calc("net set", 45, 15, usd_aud=1.52)
        self.assertAlmostEqual(c["gross_profit"], 28.40, places=1)
        self.assertEqual(c["break_even_cpa_aud"], 43.16)
        self.assertEqual(c["break_even_roas"], 1.58)
        self.assertEqual(c["flags"]["roas"], "n/a")
        self.assertIsNone(c["net_profit_usd"])

    def test_profitable_case_is_green(self):
        c = ue.calc("w", 50, 10, spend_aud=76, orders=5, usd_aud=1.52)
        self.assertTrue(all(v == "green" for v in c["flags"].values()), c["flags"])
        self.assertTrue(c["sufficient"])

    def test_bad_margin_and_bad_price(self):
        c = ue.calc("bad", 11.5, 11, usd_aud=1.52)
        self.assertIsNone(c["break_even_roas"])
        self.assertEqual(c["flags"]["gross_margin"], "red")
        with self.assertRaises(ValueError):
            ue.calc("x", 0, 1)


if __name__ == "__main__":
    unittest.main()
