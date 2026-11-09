import json
import os
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from helpers import BrainTest

from jarvis import essentials, memory, paths, records, routines, sync


class BrainSyncTest(BrainTest):
    """T105: the primary pushes a lean text brain to OneDrive, the standby pulls it."""

    def setUp(self):
        super().setUp()
        self.init()
        self.local = Path(tempfile.mkdtemp(prefix="jarvis-local-"))
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.environ["JARVIS_ROLE"] = "primary"
        super().tearDown()

    def put(self, rel, text="x"):
        p = paths.home() / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_push_keeps_text_and_leaves_big_stuff_out(self):
        for rel in ("files/reels/a/transcript.md", "files/reels/a/video.mp4", "files/reels/a/frames/f1.jpg",
                    "files/research/photo.png", "tts/venv/x.py", "tts/models/k.onnx", "tools/cf.exe",
                    "backups/old.zip", "secrets/telegram.json", "skills/blake/SKILL.md", "skills/blake/ex.png",
                    "work/general/note.md"):
            self.put(rel)
        res = sync.push(self.conn)
        self.assertTrue(res["pushed"])
        names = set(zipfile.ZipFile(self.sync_dir / sync.BRAIN_ZIP).namelist())
        for kept in ("jarvis.db", "files/reels/a/transcript.md", "skills/blake/SKILL.md", "skills/blake/ex.png",
                     "work/general/note.md"):
            self.assertIn(kept, names)
        for left in ("files/reels/a/video.mp4", "files/reels/a/frames/f1.jpg", "files/research/photo.png",
                     "tts/venv/x.py", "tts/models/k.onnx", "tools/cf.exe", "backups/old.zip",
                     "secrets/telegram.json"):
            self.assertNotIn(left, names)
        self.assertEqual(zipfile.ZipFile(self.sync_dir / sync.SECRETS_ZIP).namelist(), ["secrets/telegram.json"])
        self.assertFalse(sync.push(self.conn)["pushed"])  # nothing changed: no new upload
        self.assertEqual(sorted(p.name for p in self.sync_dir.iterdir()),
                         sorted([sync.BRAIN_ZIP, sync.SECRETS_ZIP, sync.MANIFEST]))

    def test_roles_guard_push_and_pull(self):
        with self.assertRaises(sync.SyncError):
            sync.pull(self.conn)
        paths.set_machine_role("standby")
        os.environ.pop("JARVIS_ROLE", None)
        with self.assertRaises(sync.SyncError):
            sync.push(self.conn)

    def test_standby_pulls_newer_brain_and_keeps_its_own_settings(self):
        mid = memory.remember(self.conn, title="Owner likes short replies", body="Keep it brief.",
                              category="preferences", provenance="user")[0].meta["id"]
        self.put("work/general/plan.md", "from the PC")
        self.put("secrets/telegram.json", "{}")
        paths.set_config("sip_bind", "100.89.49.110")
        sync.push(self.conn)

        # The laptop: an empty brain of its own, its own sip_bind, standby role.
        laptop = Path(tempfile.mkdtemp(prefix="jarvis-laptop-"))
        with mock.patch.dict(os.environ, {"JARVIS_HOME": str(laptop)}):
            from jarvis import db
            conn = db.connect()
            try:
                paths.set_config("sip_bind", "100.1.2.3")
                paths.set_machine_role("standby")
                os.environ.pop("JARVIS_ROLE", None)
                ticked = routines.tick(conn)
                self.assertEqual(ticked[0]["routine"], "brain-pull")
                self.assertTrue(ticked[0]["ok"], ticked)
                self.assertEqual((laptop / "work/general/plan.md").read_text(encoding="utf-8"), "from the PC")
                self.assertTrue((laptop / "secrets/telegram.json").exists())
                self.assertEqual(json.loads((laptop / "config.json").read_text(encoding="utf-8"))["sip_bind"],
                                 "100.1.2.3")
                # The laptop's brain lives elsewhere: its memory index must keep every id, not re-id the files.
                hits = [r["id"] for r in memory.search(conn, "short replies")]
                self.assertEqual(hits, [mid])
                self.assertEqual(routines.tick(conn), [])  # already up to date: quiet
                self.assertFalse(sync.status()["behind"])
            finally:
                conn.close()
                os.environ["JARVIS_ROLE"] = "primary"

    def test_essentials_digest_covers_the_essentials(self):
        memory.remember(self.conn, title="Owner wants fewer questions", body="Routine work without asking.",
                        category="preferences", provenance="user")
        records.task_add(self.conn, "Launch the face brush test")
        records.decision_add(self.conn, "PC is primary, laptop standby", rationale="one answer")
        path = essentials.write(self.conn)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.name, "ESSENTIALS.md")
        for heading in ("Standing rules", "Owner preferences", "Accounts and how each connects", "Open tasks",
                        "Products and running tests", "Money", "Key decisions", "Where everything lives"):
            self.assertIn(f"## {heading}", text)
        self.assertIn("Owner wants fewer questions", text)
        self.assertIn("Launch the face brush test", text)
        self.assertIn("PC is primary, laptop standby", text)
        self.assertLess(len(text.splitlines()), 160)

    def test_brain_sync_routine_writes_essentials_and_pushes(self):
        entry = routines.run_now(self.conn, "brain-sync")
        self.assertTrue(entry["ok"], entry)
        self.assertTrue((paths.home() / "ESSENTIALS.md").exists())
        self.assertIn("ESSENTIALS.md", zipfile.ZipFile(self.sync_dir / sync.BRAIN_ZIP).namelist())
