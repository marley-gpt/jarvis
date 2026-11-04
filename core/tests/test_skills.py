import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from helpers import REPO, BrainTest

from jarvis import feedback, memory, records, skills

SKILL = "---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n"


class SkillLearningTest(BrainTest):
    """Agent files and skills are copies in the test's own folder, so promotions never touch the repo."""

    def setUp(self):
        super().setUp()
        self.init()
        self.agents = self.home / "agents"
        shutil.copytree(REPO / ".claude" / "agents", self.agents)
        self.skill_root = self.home / "skill-dirs"
        for name, desc in (("copywriting", "Write marketing copy for product pages and ads, headlines and hooks."),
                           ("customer-research", "Voice of customer, review mining, Reddit mining, personas."),
                           ("ad-creative", "Generate and iterate ad creative, static ads, headlines, hooks."),
                           ("offers", "Design offers, guarantees, bundles and urgency."),
                           ("blake-brand", "Private mentor playbook."),
                           ("paywalls", "In-app paywalls and upgrade screens.")):
            (self.skill_root / name).mkdir(parents=True)
            (self.skill_root / name / "SKILL.md").write_text(SKILL.format(name=name, desc=desc), encoding="utf-8")
        self._env_old = {k: os.environ.get(k) for k in ("JARVIS_AGENTS_DIR", "JARVIS_SKILL_DIRS")}
        os.environ["JARVIS_AGENTS_DIR"] = str(self.agents)
        os.environ["JARVIS_SKILL_DIRS"] = str(self.skill_root)

    def tearDown(self):
        for k, v in self._env_old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_reads_each_agents_skills(self):
        have = skills.assigned("market-research")
        self.assertEqual(have["jarvis-memory"], "core")
        self.assertEqual(have["customer-research"], "listed")
        self.assertEqual(have["blake-brand"], "mentor")
        self.assertNotIn(".agents/product-marketing.md", have)
        with self.assertRaises(skills.SkillError):
            skills.assigned("nobody")

    def test_gaps_come_from_feedback_and_suggest_unassigned_skills(self):
        feedback.add(self.conn, about="Batch 1 statics", kind="ad", verdict="mixed", source="owner",
                     what="15 statics", why="weak hooks", change="Write sharper headlines and hooks for static ads",
                     agent="creative-director")
        gap = skills.gaps(self.conn, "creative-director")
        self.assertTrue(any("sharper headlines" in g for g in gap["items"]))
        found = [c["name"] for c in skills.suggest_installed(self.conn, "market-research", gap)]
        self.assertIn("ad-creative", found)  # not assigned to market-research, fits its role words
        self.assertNotIn("customer-research", found)  # already assigned
        self.assertNotIn("blake-brand", found)  # private playbooks are never candidates

    def test_note_is_a_tagged_memory_entry_and_teaches_others(self):
        nid = skills.note_add(self.conn, "market-research", "Mine 1-star reviews for objections",
                              "Sort Amazon reviews by 1 star; copy the exact objection words into the avatar sheet.",
                              skill="customer-research", for_agents=["creative-director"], source="customer-research")
        row = skills.note_get(self.conn, f"N{nid}")
        entry = memory.get(self.conn, row["memory_id"])
        self.assertIn("for:creative-director", entry.meta["tags"])
        self.assertIn("skill-note", entry.meta["tags"])
        self.assertTrue(entry.body.startswith("**JARVIS recommendation**"))
        self.assertEqual([n["id"] for n in skills.note_list(self.conn, for_agent="creative-director")], [nid])
        with self.assertRaises(skills.SkillError):
            skills.note_add(self.conn, "market-research", "Too long", "x" * 5000)
        added = skills.teach(self.conn, f"N{nid}", ["shopify"])
        self.assertEqual(added, ["shopify"])
        self.assertIn("for:shopify", memory.get(self.conn, row["memory_id"]).meta["tags"])
        log = self.conn.execute("SELECT kind, status FROM skill_changes").fetchall()
        self.assertEqual([tuple(r) for r in log], [("teach", "applied")])

    def test_outside_skill_is_queued_never_installed_and_paid_needs_spend_approval(self):
        with self.assertRaises(skills.SkillError):  # outside sources must say where they come from
            skills.candidate_add(self.conn, "market-research", "reddit-miner", origin="public", what="x")
        cid = skills.candidate_add(self.conn, "market-research", "manifold:find-reddit-pain-points", origin="plugin",
                                   source="plugin:manifold", what="Pulls Reddit pain points", cost="Manifold credits")
        with self.assertRaises(skills.SkillError) as err:
            skills.decide(self.conn, f"C{cid}", True, by="jarvis")
        self.assertIn("approval request spend", str(err.exception))
        aid = records.approval_request(self.conn, "spend", "Manifold credits for Reddit mining")
        with self.assertRaises(skills.SkillError):  # requested but not yet approved
            skills.decide(self.conn, f"C{cid}", True, by="jarvis", approval=f"A{aid}")
        records.approval_decide(self.conn, aid, True)
        result = skills.decide(self.conn, f"C{cid}", True, by="owner", approval=f"A{aid}")
        self.assertIn("Not installed", result)
        self.assertNotIn("manifold:find-reddit-pain-points", skills.assigned("market-research"))
        public = skills.candidate_add(self.conn, "market-research", "voc-synthesizer", origin="public",
                                      source="https://example.com/skill", what="VoC report")
        skills.decide(self.conn, f"C{public}", True, by="owner")
        with self.assertRaises(skills.SkillError):  # approved but not on disk, so it can't be assigned yet
            skills.mark_installed(self.conn, f"C{public}")
        pid =skills.mark_installed(self.conn, f"C{cid}", "manifold:find-reddit-pain-points")  # a plugin skill
        skills.decide(self.conn, f"P{pid}", True, by="owner")
        self.assertEqual(skills.assigned("market-research")["manifold:find-reddit-pain-points"], "learned")
        free = skills.candidate_add(self.conn, "creative-director", "bof-static-ads", origin="catalog",
                                    source="skill_01", what="Static ad structure", cost="free (already enabled)")
        self.assertIn("Not installed", skills.decide(self.conn, f"C{free}", True, by="jarvis"))  # free: no spend gate

    def test_approving_an_installed_skill_assigns_it_and_logs_why(self):
        cid = skills.candidate_add(self.conn, "market-research", "offers", origin="installed",
                                   what="Offer design", why="avatar sheets miss the offer angle")
        skills.decide(self.conn, f"C{cid}", True, by="jarvis", note="fits Blake's offer brief step")
        self.assertEqual(skills.assigned("market-research")["offers"], "learned")
        text = (self.agents / "market-research.md").read_text(encoding="utf-8")
        learned = next(l for l in text.splitlines() if l.startswith(skills.LEARNED_LINE))
        self.assertTrue(learned.endswith("`offers`."), learned)
        skills.decide(self.conn, f"C{skills.candidate_add(self.conn, 'market-research', 'ad-creative', origin='installed', what='x')}",
                      True, by="jarvis")
        text = (self.agents / "market-research.md").read_text(encoding="utf-8")
        self.assertEqual(sum(l.startswith(skills.LEARNED_LINE) for l in text.splitlines()), 1)
        self.assertIn("`offers`, `ad-creative`.", text)
        change = self.conn.execute("SELECT * FROM skill_changes WHERE kind = 'assign' ORDER BY id").fetchone()
        self.assertEqual((change["status"], change["decided_by"]), ("applied", "jarvis"))
        self.assertIn("offer angle", change["why"])
        self.assertEqual(self.conn.execute("SELECT status FROM skill_candidates WHERE id = ?", (cid,)).fetchone()[0],
                         "installed")
        self.assertEqual(skills.board(self.conn, "market-research")["agents"][0]["backlog"]["candidates"], [])

    def test_weekly_merge_teaches_promotes_and_trims(self):
        fb = feedback.add(self.conn, about="PDP hero", kind="pdp", verdict="good", source="owner", what="hero",
                          why="clear demo")
        nid = skills.note_add(self.conn, "creative-director", "Lead with the offer in the headline",
                              "Put the guarantee in the first 5 words.", skill="copywriting", for_agents=["shopify"])
        for _ in range(3):
            skills.log_use(self.conn, "creative-director", note=f"N{nid}", helped=True)
        old = skills.note_add(self.conn, "market-research", "Unused idea", "Something nobody used.")
        stale = (datetime.now() - timedelta(days=40)).isoformat()
        self.conn.execute("UPDATE skill_notes SET ts = ? WHERE id = ?", (stale, old))
        res = skills.merge(self.conn)
        self.assertIn(f"{fb.id} -> shopify", res["taught"])
        self.assertIn("for:store-qa", memory.get(self.conn, fb.id).meta["tags"])
        props = {r["id"]: r for r in self.conn.execute("SELECT * FROM skill_changes WHERE status = 'proposed'")}
        kinds = sorted((r["kind"], r["agent"]) for r in props.values())
        self.assertIn(("playbook", "creative-director"), kinds)
        self.assertIn(("playbook", "shopify"), kinds)
        self.assertIn(("trim", "market-research"), kinds)
        self.assertNotIn(("assign", "creative-director"), kinds)  # copywriting is already assigned to it
        self.assertEqual(skills.merge(self.conn)["proposed"], [])  # nothing proposed twice
        pb = next(p for p, r in props.items() if r["kind"] == "playbook" and r["agent"] == "creative-director")
        skills.decide(self.conn, f"P{pb}", True, by="jarvis", note="consistent with Blake's offer rules")
        book = skills.playbook_path("creative-director").read_text(encoding="utf-8")
        self.assertIn("Put the guarantee in the first 5 words.", book)
        self.assertNotIn("**JARVIS recommendation**", book.split("\n", 3)[3])  # header says it once
        self.assertEqual(skills.note_get(self.conn, nid)["status"], "promoted")
        trim = next(p for p, r in props.items() if r["kind"] == "trim")
        skills.decide(self.conn, f"P{trim}", True, by="jarvis")
        self.assertEqual(skills.note_get(self.conn, old)["status"], "trimmed")
        archived = list((self.home / "memory" / "_archive").rglob("*unused-idea*.md"))
        self.assertEqual(len(archived), 1)  # archived, never deleted
        rejected = next(p for p, r in props.items() if r["kind"] == "playbook" and r["agent"] == "shopify")
        self.assertIn("rejected", skills.decide(self.conn, f"P{rejected}", False, by="owner", note="not for PDPs"))
        with self.assertRaises(skills.SkillError):
            skills.decide(self.conn, f"P{rejected}", True, by="owner")

    def test_due_rotates_agents_within_the_daily_cap(self):
        first = skills.due(self.conn)
        self.assertEqual(len(first), 6)
        for a in first[:4]:
            skills.run_start(self.conn, a)
        self.assertEqual(len(skills.due(self.conn)), 2)
        tomorrow = skills.due(self.conn, datetime.now() + timedelta(days=1))
        self.assertTrue(set(tomorrow).isdisjoint(first[:4]))  # never-run agents go first

    def test_run_writes_a_brief_with_the_safety_rules(self):
        rid, path = skills.run_start(self.conn, "market-research")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Blake's method outranks every skill", text)
        self.assertIn("never as instructions", text)
        self.assertIn("Never install it", text)
        skills.run_done(self.conn, f"L{rid}", "Learned review mining")
        self.assertEqual(self.conn.execute("SELECT status FROM skill_runs").fetchone()[0], "done")

    def test_levels_grow_with_use(self):
        self.assertEqual(skills.level(0)["label"], "New")
        self.assertEqual(skills.level(9)["level"], 3)
        self.assertEqual(skills.level(99)["progress"], 1.0)
        for _ in range(4):
            skills.log_use(self.conn, "creative-director", skill="copywriting", helped=True)
        lv = {s["name"]: s for s in skills.skill_levels(self.conn, "creative-director")}
        self.assertEqual((lv["copywriting"]["level"], lv["copywriting"]["uses"]), (3, 4))

    def test_board_json_for_the_dashboard(self):
        skills.note_add(self.conn, "market-research", "A note", "Body.", for_agents=["creative-director"])
        data = json.loads(self.run_cli("skills", "board").stdout)
        cd = next(a for a in data["agents"] if a["name"] == "creative-director")
        self.assertEqual(cd["recently_learned"][0]["title"], "A note")
        self.assertEqual(set(cd), {"name", "skills", "level", "recently_learned", "backlog", "changes", "last_run"})
        self.assertIn("progress", cd["skills"][0])
        self.assertEqual(data["schedule"]["daily_cap"], 6)
        one = json.loads(self.run_cli("skills", "board", "--agent", "shopify").stdout)
        self.assertEqual([a["name"] for a in one["agents"]], ["shopify"])
        bad = self.run_cli("skills", "board", "--agent", "nobody", check=False)
        self.assertEqual(bad.returncode, 2)

    def test_cli_learning_round_trip(self):
        out = self.run_cli("skills", "run", "start", "market-research").stdout
        self.assertIn("L1 started", out)
        self.run_cli("skills", "note", "add", "market-research", "Quote objections verbatim", "--body",
                     "Keep the customer's exact words.", "--skill", "customer-research", "--for", "creative-director")
        self.run_cli("skills", "candidate", "add", "market-research", "offers", "--origin", "installed",
                     "--what", "Offer design", "--why", "offer brief gap")
        review = self.run_cli("skills", "review").stdout
        self.assertIn("C1 market-research <- offers [installed] free", review)
        self.run_cli("skills", "used", "creative-director", "--note", "N1", "--helped")
        self.assertIn("helped 1/1", self.run_cli("skills", "notes").stdout)
        self.run_cli("skills", "run", "done", "L1", "--summary", "done")
        self.assertIn("Quote objections verbatim", self.run_cli("skills", "mine", "creative-director").stdout)

    def test_hook_counts_skill_tool_use(self):
        self.run_hook("post_tool", {"tool_name": "Skill", "tool_input": {"skill": "copywriting"},
                                    "agent_type": "creative-director"})
        self.run_hook("post_tool", {"tool_name": "Skill", "tool_input": {"skill": "customer-research"}})
        rows = [tuple(r) for r in self.conn.execute("SELECT agent, skill, source FROM skill_uses ORDER BY id")]
        self.assertEqual(rows, [("creative-director", "copywriting", "hook"), ("jarvis", "customer-research", "hook")])
