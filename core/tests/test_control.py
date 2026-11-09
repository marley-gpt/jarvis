import base64
import json
import os
import threading
import time
import unittest.mock

from helpers import BrainTest

from jarvis import control, db, jobs, paths, work
from jarvis.dashboard import Hub


class PermissionsTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        self.hub = Hub()
        self.q = self.hub.subscribe()
        self.pushed = []
        self.perms = control.Permissions(self.hub, push=self.pushed.append)

    def ask_later(self, req, rid="r1"):
        out = {}
        t = threading.Thread(target=lambda: out.update(self.perms.ask(rid, req)), daemon=True)
        t.start()
        deadline = time.time() + 5
        while not self.perms.listing() and time.time() < deadline:
            time.sleep(0.02)
        return t, out

    def test_read_only_tools_pass_without_a_record(self):
        out = self.perms.ask("r0", {"tool_name": "Read", "input": {"file_path": "x"}})
        self.assertEqual(out, {"behavior": "allow", "updatedInput": {"file_path": "x"}})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_a_prompt_becomes_an_approval_and_the_owner_decides(self):
        t, out = self.ask_later({"tool_name": "mcp__Shopify__update-product", "input": {"title": "New"}})
        waiting = self.perms.listing()
        self.assertEqual(len(waiting), 1)
        row = db.connect().execute("SELECT * FROM approvals WHERE id = ?", (waiting[0]["approval"],)).fetchone()
        self.assertEqual((row["kind"], row["status"]), ("tool", "pending"))
        self.assertIn("update-product", row["summary"])
        self.assertEqual(len(self.pushed), 1)  # to the phone with buttons
        self.assertEqual(self.perms.answer(None, True)["status"], "approved")
        t.join(5)
        self.assertEqual(out["behavior"], "allow")
        self.assertEqual(self.perms.listing(), [])
        kinds = [self.q.get_nowait()["type"] for _ in range(self.q.qsize())]
        self.assertEqual(kinds, ["permission", "permission_done"])

    def test_rejected_elsewhere_is_seen_and_denied(self):
        from jarvis import records
        t, out = self.ask_later({"tool_name": "Write", "input": {"file_path": "C:/outside.txt"}})
        aid = self.perms.listing()[0]["approval"]
        records.approval_decide(db.connect(), aid, False, "from the phone")
        t.join(5)
        self.assertEqual(out["behavior"], "deny")
        self.assertIn(f"A{aid}", out["message"])

    def test_no_answer_in_time_is_a_refusal(self):
        (self.home / "config.json").write_text(json.dumps({"permission_wait_seconds": 0.3}), encoding="utf-8")
        out = self.perms.ask("r2", {"tool_name": "Write", "input": {}})
        self.assertEqual(out["behavior"], "deny")
        self.assertEqual(self.conn.execute("SELECT status, decision_note FROM approvals").fetchone()[:],
                         ("rejected", "no answer in time"))

    def test_voice_yes_and_no(self):
        self.assertIsNone(self.perms.voice("yes"))  # nothing waiting: it's an ordinary message
        t, out = self.ask_later({"tool_name": "Write", "input": {}})
        self.assertIsNone(self.perms.voice("yes please write the brief for the shop"))
        self.assertEqual(self.perms.voice("No, sir.")["status"], "rejected")
        t.join(5)
        self.assertEqual(out["behavior"], "deny")

    def test_a_question_is_answered_without_opening_the_clearance_window(self):
        from jarvis import records
        req = {"tool_name": "AskUserQuestion", "input": {"questions": [{"question": "Which colour?"}]}}
        t, out = self.ask_later(req)
        self.assertTrue(self.perms.listing()[0]["question"])
        self.assertIsNone(self.perms.voice("yes"))  # a question needs a real answer
        self.perms.answer(None, answers={"Which colour?": "Blue"})
        t.join(5)
        self.assertEqual(out["updatedInput"]["answers"], {"Which colour?": "Blue"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        self.assertIsNone(records.grant_active())


class SessionsTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.dir = self.home / "sessions"
        self.dir.mkdir()
        (self.home / "config.json").write_text(json.dumps({"claude_sessions_dir": str(self.dir)}), encoding="utf-8")
        self.sid = "0915e91d-3452-4129-8287-2bec8aab6132"
        lines = [
            {"type": "queue-operation"},
            {"type": "user", "message": {"content": "<command-name>x</command-name>"}},
            {"type": "user", "timestamp": "2026-11-09T01:00:00Z", "message": {"content": "Start the ads for the brush"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Very good."},
                                                         {"type": "tool_use", "name": "Bash", "input": {"command": "bin/jarvis task list"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "user", "isSidechain": True, "message": {"content": "agent brief"}},
        ]
        (self.dir / f"{self.sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
        (self.dir / "not-a-session.jsonl").write_text("{}", encoding="utf-8")

    def test_listing_and_transcript(self):
        found = control.sessions(current=self.sid)
        self.assertEqual([s["id"] for s in found], [self.sid])
        self.assertEqual(found[0]["title"], "Start the ads for the brush")
        self.assertTrue(found[0]["current"])
        said = control.transcript(self.sid)
        self.assertEqual([m["role"] for m in said], ["owner", "jarvis", "tool"])
        self.assertIn("bin/jarvis task list", said[2]["text"])
        with self.assertRaises(ValueError):
            control.transcript("../../etc/passwd")

    def test_default_folder_follows_claude_codes_naming(self):
        (self.home / "config.json").write_text("{}", encoding="utf-8")
        self.assertNotRegex(control.sessions_dir().name, r"[^A-Za-z0-9-]")


class FilesAndPcTest(BrainTest):
    def test_attached_files_are_saved_and_programs_refused(self):
        saved = control.save_files([{"name": "../../brief notes.pdf", "data": base64.b64encode(b"%PDF").decode()}])
        self.assertEqual(len(saved), 1)
        self.assertIn("files/uploads/", saved[0])
        self.assertTrue(saved[0].endswith("brief notes.pdf"))
        self.assertEqual(open(saved[0], "rb").read(), b"%PDF")  # an absolute path here: the test brain is outside the repo
        for bad in ([{"name": "x.exe", "data": "AA=="}], [{"name": "x.txt", "data": "!!"}], [{"name": "x"}] * 5):
            with self.assertRaises(ValueError):
                control.save_files(bad)

    def test_open_apps_folders_files_and_pages(self):
        launched = []
        self.assertEqual(control.open_target("notepad", launch=launched.append), "app notepad")
        self.assertEqual(launched[-1], ["notepad.exe"])
        self.assertTrue(control.open_target("core", launch=launched.append).startswith("folder"))
        self.assertEqual(launched[-1][0], "explorer.exe")
        with unittest.mock.patch("webbrowser.open") as web:
            control.open_target("https://example.com", launch=launched.append)
            web.assert_called_once_with("https://example.com")
        with unittest.mock.patch("os.startfile", create=True) as start:
            self.assertEqual(control.open_target("README.md", launch=launched.append), "file README.md")
            start.assert_called_once()
        for bad in ("", "no-such-thing-anywhere", "bin/jarvis.cmd"):
            with self.assertRaises(ValueError):
                control.open_target(bad, launch=launched.append)

    def test_extra_apps_come_from_config(self):
        (self.home / "config.json").write_text(json.dumps({"pc_apps": {"Canva": "C:/canva.exe"}}), encoding="utf-8")
        launched = []
        control.open_target("canva", launch=launched.append)
        self.assertEqual(launched, [["C:/canva.exe"]])


class WorkersAndJobsTest(BrainTest):
    def make_run(self, wid, log_lines, status="running", pid=None):
        run = {"id": wid, "task": "T1", "agent": "operations", "brief": "Do it", "session": "s", "status": status,
               "log": str(work.runs_dir() / f"W{wid}.log"), "pid": pid, "started": "now"}
        (work.runs_dir() / f"W{wid}.log").write_text("\n".join(log_lines), encoding="utf-8")
        work._save(run)
        return run

    def test_tail_reads_stream_json_and_old_json_logs(self):
        stream = [json.dumps({"type": "system", "subtype": "init"}),
                  json.dumps({"type": "assistant", "message": {"content": [
                      {"type": "text", "text": "Starting."},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "bin/jarvis task list"}}]}}),
                  json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}}),
                  json.dumps({"type": "result", "is_error": False, "result": "All done"})]
        self.make_run(1, stream, status="done")
        lines = work.tail(1)["lines"]
        self.assertEqual(lines, ["Starting.", "> Bash: bin/jarvis task list", "Finished: All done"])
        self.assertTrue(work._finished(work.get(1)))  # a tool error earlier doesn't make the run a failure
        self.make_run(2, [json.dumps({"type": "result", "is_error": True, "result": "boom"})], status="done")
        self.assertFalse(work._finished(work.get(2)))
        self.assertEqual(work.tail(2)["lines"], ["Finished with an error: boom"])
        self.make_run(3, [json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "half"}]}})], status="done")
        self.assertFalse(work._finished(work.get(3)))

    def test_stop_marks_cancelled_so_it_is_not_resumed(self):
        self.make_run(4, [], pid=999999)
        with unittest.mock.patch.object(work, "_alive", return_value=True), \
                unittest.mock.patch("subprocess.run") as kill, unittest.mock.patch("os.killpg", create=True):
            run = work.stop("W4")
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(work.resume(), [])
        with self.assertRaises(work.WorkError):
            work.stop("W4")

    def test_job_cancel(self):
        self.init()
        jid = jobs.add(self.conn, "Open Kalodata and find brushes")
        self.assertEqual(jobs.cancel(self.conn, jid)["status"], "cancelled")
        with self.assertRaises(jobs.JobError):
            jobs.cancel(self.conn, jid)
