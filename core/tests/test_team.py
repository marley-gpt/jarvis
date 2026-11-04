import json
import os
import shutil

from helpers import REPO, BrainTest

from jarvis import records, skills, team

BODY = "You mine 1-star reviews for objections. Quote verbatim with the source URL."


class TeamTest(BrainTest):
    """Agent files are copies in the test's own folder, so hiring never touches the repo."""

    def setUp(self):
        super().setUp()
        self.agents = self.home / "agents-dir"
        shutil.copytree(REPO / ".claude" / "agents", self.agents)
        self._old = os.environ.get("JARVIS_AGENTS_DIR")
        os.environ["JARVIS_AGENTS_DIR"] = str(self.agents)
        self.told = []

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JARVIS_AGENTS_DIR", None)
        else:
            os.environ["JARVIS_AGENTS_DIR"] = self._old
        super().tearDown()

    def propose(self, name="review-miner", **kw):
        args = dict(role="Mines product reviews for objections.", body=BODY, tools=["Bash", "Read", "WebFetch"])
        args.update(kw)
        return team.propose(self.conn, "market-research", name, **args)

    def test_sub_agents_are_logged_from_queue_to_result(self):
        sid = team.delegate(self.conn, "market-research", "Mine r/SkincareAddiction for brush objections",
                            child="creative-research", task="T60")
        team.delegate(self.conn, "creative-director", "QC statics 8-15")
        self.assertEqual([r["id"] for r in team.queued(self.conn, 1)], [sid])
        team.sub_update(self.conn, f"S{sid}", "running")
        board = team.board(self.conn)
        self.assertEqual(board["sub_agents"]["running"][0]["child"], "creative-research")
        self.assertEqual(len(board["sub_agents"]["queued"]), 1)
        team.sub_update(self.conn, f"S{sid}", "done", "42 quotes saved to the VoC bank")
        done = team.board(self.conn)["sub_agents"]["recent"][0]
        self.assertEqual((done["parent"], done["result"]), ("market-research", "42 quotes saved to the VoC bank"))
        with self.assertRaises(skills.SkillError):
            team.delegate(self.conn, "nobody", "x")
        with self.assertRaises(team.TeamError):
            team.sub_update(self.conn, "S99", "done")

    def test_within_role_free_hire_is_jarvis_reviewed_added_announced_and_undoable(self):
        hid, tier, reasons = self.propose()
        self.assertEqual((tier, reasons), ("jarvis", []))
        self.assertIn("added", team.approve(self.conn, hid, by="jarvis", notify=self.told.append))
        self.assertIn("review-miner", skills.agent_names())
        self.assertEqual(team.team_of(self.conn, "review-miner"), "research")
        self.assertIn("review-miner", self.told[0])
        text = (self.agents / "review-miner.md").read_text(encoding="utf-8")
        self.assertIn("tools: Bash, Read, WebFetch", text)
        self.assertIn("skills: [jarvis-memory, jarvis-feedback-loop]", text)
        self.assertIn("review-miner", [h["name"] for t in team.board(self.conn)["teams"] for h in t["hired"]])
        team.remove(self.conn, "review-miner", by="owner", why="not needed")
        self.assertNotIn("review-miner", skills.agent_names())
        self.assertEqual(len(list((self.home / "agents" / "removed").glob("review-miner-*.md"))), 1)
        with self.assertRaises(team.TeamError):  # only hired agents can be removed here
            team.remove(self.conn, "shopify", by="owner", why="x")

    def test_new_tools_paid_or_other_team_go_to_the_owner_first(self):
        hid, tier, reasons = self.propose(tools=["Bash", "mcp__Shopify__graphql_mutation"])
        self.assertEqual(tier, "owner")
        self.assertIn("mcp__Shopify__graphql_mutation", reasons[0])
        aid = team.hire_get(self.conn, hid)["approval_id"]
        row = self.conn.execute("SELECT kind, status FROM approvals WHERE id = ?", (aid,)).fetchone()
        self.assertEqual(tuple(row), ("other", "pending"))
        with self.assertRaises(team.TeamError):  # even JARVIS can't add it before the owner says yes
            team.approve(self.conn, hid, by="jarvis", notify=self.told.append)
        records.approval_decide(self.conn, aid, True)
        team.approve(self.conn, hid, by="owner", notify=self.told.append)
        self.assertIn("review-miner", skills.agent_names())
        self.assertEqual(self.propose("paid-miner", cost="US$29/month")[1], "owner")
        self.assertEqual(self.propose("ad-spy", team="ads")[1], "owner")  # outside market-research's team

    def test_agents_cannot_rewrite_existing_agents(self):
        with self.assertRaises(team.TeamError):
            self.propose("shopify")
        self.propose()
        with self.assertRaises(team.TeamError):
            self.propose()  # already proposed
        with self.assertRaises(team.TeamError):
            self.propose("Bad Name")

    def test_rejected_hire_is_logged_and_a_failed_phone_does_not_undo_an_addition(self):
        hid, _, _ = self.propose()
        team.reject(self.conn, hid, by="jarvis", note="overlaps market-research")
        self.assertEqual(team.hire_get(self.conn, hid)["status"], "rejected")
        hid, _, _ = self.propose("review-miner-2")

        def broken(text):
            raise RuntimeError("no phone")
        self.assertIn("not told: no phone", team.approve(self.conn, hid, by="jarvis", notify=broken))
        self.assertIn("review-miner-2", skills.agent_names())

    def test_cli_and_board(self):
        self.run_cli("team", "delegate", "market-research", "Mine Amazon 1-star reviews")
        self.assertIn("S1 market-research -> market-research", self.run_cli("team", "next").stdout)
        self.run_cli("team", "start", "S1")
        self.run_cli("team", "done", "S1", "--result", "30 quotes")
        self.assertIn("result: 30 quotes", self.run_cli("team", "subs").stdout)
        out = self.run_cli("team", "propose", "market-research", "review-miner", "--role", "Mines reviews",
                           "--tools", "Bash,Read", "--body", BODY).stdout
        self.assertIn("H1 drafted", out)
        self.assertIn("JARVIS can approve", self.run_cli("team", "review").stdout)
        data = json.loads(self.run_cli("team", "board").stdout)
        self.assertEqual(data["hires"]["proposed"][0]["id"], "H1")
        self.assertIn("allowed_runs", data["pace"])
        self.assertEqual({t["name"] for t in data["teams"]}, set(team.TEAMS))
