import os
import time

from helpers import BrainTest

from jarvis import memory, paths


class MemoryTest(BrainTest):
    def add(self, title, body, **kw):
        kw.setdefault("category", "mentor")
        kw.setdefault("provenance", "mentor")
        kw.setdefault("source", "Lesson 4 - Ad research")
        return memory.remember(self.conn, title=title, body=body, **kw)

    def test_init_seeds_business_knowledge_once(self):
        self.init()
        self.init()
        st = memory.stats(self.conn)
        self.assertEqual(st["total"], 5)
        hits = memory.search(self.conn, "private label packaging")
        self.assertEqual(hits[0]["id"], "M-SEED-0002")

    def test_add_and_search_with_citation(self):
        entry, _ = self.add("How to find winning Meta ads",
                            "Filter the Ad Library by active ads, sort by longest running, check 5+ variations.",
                            source_ref="00:12:31", tags=["meta", "ad-research"])
        self.assertTrue(entry.path.exists())
        self.assertEqual(entry.path.parent.name, "mentor")
        hits = memory.search(self.conn, "winning meta ads research")
        self.assertEqual(hits[0]["id"], entry.id)
        self.assertEqual(hits[0]["source_ref"], "00:12:31")
        self.assertEqual(memory.search(self.conn, "winning ads", category="shopify"), [])

    def test_or_fallback_when_not_all_words_match(self):
        self.add("Ad Library research", "Sort by longest running ads.")
        self.assertTrue(memory.search(self.conn, "longest running zebra"))

    def test_query_punctuation_is_safe(self):
        self.add("CPA rule", "Kill at 2x CPA.")
        for q in ('"unbalanced', "a AND OR NOT", "CPA*:(", "", "-"):
            memory.search(self.conn, q)  # must not raise an FTS syntax error

    def test_mentor_knowledge_requires_source(self):
        with self.assertRaises(memory.MemoryError):
            memory.remember(self.conn, title="x", body="y", category="mentor", provenance="mentor")

    def test_duplicate_refused_unless_forced(self):
        body = "Our test budget is fifty US dollars per product over three days."
        self.add("Test budget per product", body, category="business", provenance="user")
        with self.assertRaises(memory.MemoryError) as ctx:
            self.add("Test budget per product", body, category="business", provenance="user")
        self.assertIn("supersede", str(ctx.exception))
        self.add("Test budget per product", body, category="business", provenance="user", force=True)

    def test_supersede_hides_old_version(self):
        old, _ = self.add("Kill rule", "Kill an ad set after $20 with no add to cart.", source_ref="10:00")
        new, _ = memory.remember(self.conn, title="Kill rule", body="Kill after $25 with no add to cart.",
                                 category="mentor", provenance="user", source="Owner correction",
                                 supersedes=old.id)
        hits = memory.search(self.conn, "kill add to cart")
        self.assertEqual([h["id"] for h in hits], [new.id])
        old_now = memory.get(self.conn, old.id)
        self.assertEqual(old_now.meta["status"], "superseded")
        self.assertEqual(old_now.meta["superseded_by"], new.id)
        self.assertEqual(len(memory.search(self.conn, "kill add to cart", include_inactive=True)), 2)

    def test_hand_written_file_is_normalised_and_indexed(self):
        folder = paths.memory_dir() / "shopify"
        folder.mkdir(parents=True)
        (folder / "theme.md").write_text("# Shrine theme notes\n\nThe store uses the Shrine theme v1.4.\n",
                                         encoding="utf-8")
        hits = memory.search(self.conn, "shrine theme")
        self.assertEqual(hits[0]["category"], "shopify")
        self.assertEqual(hits[0]["title"], "Shrine theme notes")
        meta, _ = memory.parse((folder / "theme.md").read_text(encoding="utf-8"))
        self.assertTrue(meta["id"].startswith("M-"))

    def test_hand_edit_is_reindexed(self):
        entry, _ = self.add("Hook formula", "Lead with the pain point.")
        text = entry.path.read_text(encoding="utf-8").replace("pain point", "transformation outcome")
        entry.path.write_text(text, encoding="utf-8")
        later = time.time() + 5
        os.utime(entry.path, (later, later))
        self.assertTrue(memory.search(self.conn, "transformation outcome"))
        self.assertFalse(memory.search(self.conn, "pain point"))

    def test_copied_file_gets_new_id(self):
        entry, _ = self.add("Hook formula", "Lead with the pain point.")
        copy = entry.path.with_name("hook-copy.md")
        copy.write_text(entry.path.read_text(encoding="utf-8"), encoding="utf-8")
        memory.sync(self.conn)
        ids = {r["id"] for r in memory.listing(self.conn)}
        self.assertEqual(len(ids), 2)

    def test_archive_moves_file_and_never_deletes(self):
        entry, _ = self.add("Old offer idea", "Free shipping over $40.", category="business", provenance="user")
        archived = memory.set_status(self.conn, entry.id, "archived", "no longer relevant")
        self.assertFalse(entry.path.exists())
        self.assertTrue(archived.path.exists())
        self.assertIn("_archive", archived.path.parts)
        self.assertFalse(memory.search(self.conn, "free shipping"))

    def test_deleted_file_leaves_index(self):
        entry, _ = self.add("Temporary", "Something unique zanzibar.")
        entry.path.unlink()
        self.assertFalse(memory.search(self.conn, "zanzibar"))

    def test_front_matter_round_trip(self):
        meta = {"id": "M-1", "title": "A: b", "tags": ["x", "y"], "source_ref": "01:02:03", "category": "mentor"}
        parsed, body = memory.parse(memory.dump(meta, "Body text"))
        self.assertEqual(parsed["title"], "A: b")
        self.assertEqual(parsed["tags"], ["x", "y"])
        self.assertEqual(parsed["source_ref"], "01:02:03")
        self.assertEqual(body.strip(), "Body text")
