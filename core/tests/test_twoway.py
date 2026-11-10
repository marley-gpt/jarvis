import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from helpers import BrainTest

from jarvis import brief, db, journal, memory, paths, projects, records, routines, sync, twoway, work


class TwoWaySyncTest(BrainTest):
    """T119: the standby laptop's own work reaches the primary PC, and each sees what the other runs."""

    def setUp(self):
        super().setUp()
        self.init()
        self.tmp = [Path(tempfile.mkdtemp(prefix=f"jarvis-{n}-")) for n in ("pc-local", "laptop", "laptop-local")]
        self.pc_local, self.laptop_home, self.laptop_local = self.tmp
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.pc_local), "JARVIS_MACHINE": "marley"})
        self.env.start()
        self.laptop_conn = None

    def tearDown(self):
        if self.laptop_conn:
            self.laptop_conn.close()
        self.env.stop()
        os.environ["JARVIS_ROLE"] = "primary"
        super().tearDown()
        for d in self.tmp:
            shutil.rmtree(d, ignore_errors=True)

    @contextlib.contextmanager
    def laptop(self):
        with mock.patch.dict(os.environ, {"JARVIS_HOME": str(self.laptop_home), "LOCALAPPDATA": str(self.laptop_local),
                                          "JARVIS_MACHINE": "Marleyslaptop", "JARVIS_ROLE": "standby"}):
            if self.laptop_conn is None:
                self.laptop_conn = db.connect()
            yield self.laptop_conn

    def put(self, rel, text):
        p = paths.home() / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def run_file(self, wid, task, status="running", alive=True, machine="Marleyslaptop"):
        run = {"id": wid, "task": task, "agent": "product-research", "brief": f"Research job for {task}",
               "session": f"session-{machine}-{wid}", "log": str(work.runs_dir() / f"W{wid}.log"), "status": status,
               "started": paths.now(), "attempts": 1, "machine": machine, "pid": os.getpid() if alive else None}
        (work.runs_dir() / f"W{wid}.json").write_text(json.dumps(run), encoding="utf-8")
        return run

    def start_pair(self):
        """The PC with a task and a running worker pushes; the laptop pulls it."""
        self.pc_task = records.task_add(self.conn, "Find fresh product candidates")
        memory.remember(self.conn, title="Owner wants fewer questions", body="Routine work without asking.",
                        category="preferences", provenance="user")
        self.run_file(38, f"T{self.pc_task}", machine="marley")
        sync.push(self.conn)
        with self.laptop() as lc:
            self.assertTrue(sync.pull(lc)["pulled"])

    # ------------------------------------------------------------------ the main round trip

    def test_laptop_work_reaches_the_pc_without_clobbering(self):
        self.start_pair()
        with self.laptop() as lc:
            # The PC's worker is mirrored on the laptop, never in its own runs (so the laptop never resumes it).
            self.assertFalse((work.runs_dir() / "W38.json").exists())
            self.assertTrue((paths.work_dir() / "remote" / "marley" / "runs" / "W38.json").exists())
            records.product_add(lc, "Knee sleeve dial adjust knee brace")
            tid = records.task_add(lc, "Validate the knee sleeve", product="knee-sleeve-dial-adjust-knee-brace")
            sub = records.task_add(lc, "Supplier shortlist", parent=f"T{tid}", agent="product-research")
            self.assertGreater(tid, self.pc_task + 1)  # the laptop's ids start clear of the PC's
            did = records.decision_add(lc, "Knee sleeve goes to supplier check", rationale="demand ok", task=f"T{tid}")
            records.task_update(lc, f"T{self.pc_task}", status="in_progress", note="laptop picked it up")
            mid = memory.remember(lc, title="Knee sleeve demand is seasonal", body="Peaks in winter.",
                                  category="product-research", provenance="agent")[0].meta["id"]
            self.put("work/knee/T-dossier.md", "# Knee sleeve dossier\n")
            journal.record(lc, "owner", "start the knee sleeve research", session_id="lap1")
            self.run_file(39, f"T{sub}")
            res = twoway.export(lc)
            self.assertTrue(res["written"], res)
            self.assertFalse(twoway.export(lc)["written"])  # nothing new: OneDrive isn't churned

        # Meanwhile the PC made a task of its own.
        pc_new = records.task_add(self.conn, "PC side job")
        total = twoway.merge_all(self.conn)
        self.assertEqual(total["outboxes"], 1)
        self.assertEqual(total["remapped"], 0)
        task = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(task["title"], "Validate the knee sleeve")
        self.assertEqual(self.conn.execute("SELECT parent_id FROM tasks WHERE id = ?", (sub,)).fetchone()[0], tid)
        self.assertEqual(self.conn.execute("SELECT title FROM tasks WHERE id = ?", (pc_new,)).fetchone()[0], "PC side job")
        self.assertEqual(self.conn.execute("SELECT task_id FROM decisions WHERE id = ?", (did,)).fetchone()[0], tid)
        self.assertEqual(self.conn.execute("SELECT status FROM tasks WHERE id = ?", (self.pc_task,)).fetchone()[0],
                         "in_progress")
        self.assertEqual([r["id"] for r in memory.search(self.conn, "seasonal")], [mid])
        self.assertTrue((self.home / "work/knee/T-dossier.md").exists())
        self.assertTrue(journal.search(self.conn, "knee sleeve research"))
        mirror = self.home / "work" / "remote" / "Marleyslaptop" / "runs" / "W39.json"
        self.assertEqual(json.loads(mirror.read_text(encoding="utf-8"))["machine"], "Marleyslaptop")
        self.assertNotIn(39, [r["id"] for r in work.listing(50)])  # never resumed on the PC
        # Merging again changes nothing: the outbox was already merged.
        self.assertEqual(twoway.merge_all(self.conn)["outboxes"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        # The PC pushes the merged brain; the laptop pulls it and keeps its own run and no duplicates.
        sync.push(self.conn)
        with self.laptop() as lc:
            self.assertTrue(sync.pull(lc)["pulled"])
            self.assertEqual(lc.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], count)
            self.assertEqual(json.loads((work.runs_dir() / "W39.json").read_text(encoding="utf-8"))["session"],
                             "session-Marleyslaptop-39")
            self.assertFalse((paths.work_dir() / "remote" / "Marleyslaptop").exists())  # its own mirror stays away
            twoway.export(lc)
        self.assertEqual(twoway.merge_all(self.conn)["added"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], count)

    def test_pull_keeps_laptop_work_the_pc_has_not_merged_yet(self):
        self.start_pair()
        with self.laptop() as lc:
            tid = records.task_add(lc, "Laptop only task")
            self.put("work/general/laptop-note.md", "laptop words")
        records.task_add(self.conn, "PC moved on")
        sync.push(self.conn)  # pushed before the PC merged anything from the laptop
        with self.laptop() as lc:
            res = sync.pull(lc)
            self.assertIn("own work", res["note"])
            self.assertEqual(lc.execute("SELECT title FROM tasks WHERE id = ?", (tid,)).fetchone()[0], "Laptop only task")
            self.assertTrue(lc.execute("SELECT 1 FROM tasks WHERE title = 'PC moved on'").fetchone())
            self.assertEqual((paths.home() / "work/general/laptop-note.md").read_text(encoding="utf-8"), "laptop words")
        twoway.merge_all(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks WHERE title = 'Laptop only task'").fetchone()[0], 1)

    def test_laptop_that_pulled_before_t119_still_sends_its_own_work(self):
        self.start_pair()
        with self.laptop() as lc:
            twoway._baseline_json().unlink()  # pulled by the old code: no baseline kept
            twoway._baseline_db().unlink()
            old = self.put("work/knee/research.md", "knee sleeve research from yesterday")
            os.utime(old, (old.stat().st_mtime - 86400,) * 2)
            records.task_add(lc, "Knee sleeve research")
            res = twoway.export(lc)
            self.assertEqual((res["rows"] >= 1, res["files"]), (True, 1), res)
        twoway.merge_all(self.conn)
        self.assertTrue((self.home / "work/knee/research.md").exists())
        self.assertTrue(self.conn.execute("SELECT 1 FROM tasks WHERE title = 'Knee sleeve research'").fetchone())
        self.assertEqual(self.conn.execute("SELECT status FROM tasks WHERE id = ?", (self.pc_task,)).fetchone()[0], "todo")

    def test_taken_id_gets_a_new_one_and_is_logged(self):
        self.start_pair()
        with self.laptop() as lc:
            tid = records.task_add(lc, "Laptop task")
            twoway.export(lc)
        for _ in range(tid - self.pc_task):
            records.task_add(self.conn, "PC filler")  # the PC uses the laptop's number first
        self.assertEqual(twoway.merge_all(self.conn)["remapped"], 1)
        rows = self.conn.execute("SELECT id FROM tasks WHERE title = 'Laptop task'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][0], tid)
        log = (self.home / "work/sync/merge.jsonl").read_text(encoding="utf-8")
        self.assertIn("took a new id", log)

    # ------------------------------------------------------------------ conflicts

    def test_both_changed_newer_wins_and_conflict_is_logged(self):
        self.start_pair()
        other = records.task_add(self.conn, "Second PC task")
        sync.push(self.conn)
        with self.laptop() as lc:
            sync.pull(lc)
            lc.execute("UPDATE tasks SET status='done', updated='2026-11-11T10:00:00+11:00' WHERE id=?", (self.pc_task,))
            lc.execute("UPDATE tasks SET status='done', updated='2026-11-11T09:00:00+11:00' WHERE id=?", (other,))
            lc.commit()
            twoway.export(lc)
        self.conn.execute("UPDATE tasks SET status='blocked', updated='2026-11-11T09:30:00+11:00' WHERE id=?",
                          (self.pc_task,))
        self.conn.execute("UPDATE tasks SET status='blocked', updated='2026-11-11T09:30:00+11:00' WHERE id=?", (other,))
        self.conn.commit()
        total = twoway.merge_all(self.conn)
        self.assertEqual(total["conflicts"], 2)
        status = dict(self.conn.execute("SELECT id, status FROM tasks WHERE id IN (?,?)", (self.pc_task, other)).fetchall())
        self.assertEqual(status[self.pc_task], "done")   # the laptop's change was newer
        self.assertEqual(status[other], "blocked")       # the PC's was newer
        self.assertEqual(len(twoway.conflicts()), 2)

    def test_file_changed_on_both_keeps_the_loser_and_journals_merge(self):
        self.put("work/general/plan.md", "v1")
        day = "journal/2026-11-11.md"
        self.put(day, "# JARVIS journal 2026-11-11\n\n### 09:00 Owner\n\nmorning\n")
        self.start_pair()
        with self.laptop() as lc:
            self.put("work/general/plan.md", "laptop v2")
            self.put(day, "# JARVIS journal 2026-11-11\n\n### 09:00 Owner\n\nmorning\n\n### 11:00 Owner\n\nlaptop chat\n")
            twoway.export(lc)
        p = self.put("work/general/plan.md", "pc v2")
        os.utime(p, (p.stat().st_mtime + 3600,) * 2)  # the PC's edit is the newer one
        self.put(day, "# JARVIS journal 2026-11-11\n\n### 09:00 Owner\n\nmorning\n\n### 10:00 Owner\n\npc chat\n")
        twoway.merge_all(self.conn)
        self.assertEqual(p.read_text(encoding="utf-8"), "pc v2")
        kept = list((self.home / "work/sync/conflicts/Marleyslaptop").rglob("plan.md.*"))
        self.assertEqual([k.read_text(encoding="utf-8") for k in kept], ["laptop v2"])
        text = (self.home / day).read_text(encoding="utf-8")
        self.assertLess(text.index("pc chat"), text.index("laptop chat"))
        self.assertEqual(text.count("morning"), 1, text)

    # ------------------------------------------------------------------ what the other machine runs

    def test_brief_and_projects_show_the_other_machine(self):
        self.start_pair()
        with self.laptop() as lc:
            tid = records.task_add(lc, "Knee sleeve validation")
            self.run_file(40, f"T{tid}")
            twoway.export(lc)
            # The laptop sees the PC's worker too, once the PC has written its status.
        twoway.write_status(self.conn)
        text = brief.session_brief(self.conn)
        self.assertIn("## On the other machine", text)
        self.assertIn("W40@Marleyslaptop [running]", text)
        self.assertIn("Knee sleeve validation", text)
        twoway.merge_all(self.conn)
        out = "\n".join(projects.lines(projects.overview(self.conn)))
        self.assertIn("W40@Marleyslaptop running", out)
        self.assertIn("On the other machine:", out)
        with self.laptop() as lc:
            self.assertIn("W38@marley [running]", brief.session_brief(lc))

    def test_workers_resume_only_on_their_own_machine(self):
        self.run_file(7, "T1", status="running", alive=False, machine="Marleyslaptop")
        with mock.patch.object(work, "_spawn") as spawn:
            self.assertEqual(work.resume(), [])
            spawn.assert_not_called()

    def test_routines_merge_on_the_primary_and_send_from_the_standby(self):
        self.start_pair()
        with self.laptop() as lc:
            records.task_add(lc, "Laptop routine task")
            ticked = routines.tick(lc)
            self.assertEqual(ticked[0]["routine"], "brain-outbox")
            self.assertTrue(ticked[0]["ok"], ticked)
        entry = routines.run_now(self.conn, "brain-merge")
        self.assertTrue(entry["ok"], entry)
        self.assertIn("merged 1 outbox", entry["note"])
        self.assertTrue(self.conn.execute("SELECT 1 FROM tasks WHERE title = 'Laptop routine task'").fetchone())
        self.assertIn("last merge", sync.status_line())
