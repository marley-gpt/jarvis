import json
import os
from datetime import datetime, timedelta
from unittest import mock

from helpers import CORE, BrainTest

from jarvis import media, memory, paths, routines


class ReelsQueueTest(BrainTest):
    """`watch` queues links no route can download, and the reels-retry routine watches them later."""

    def setUp(self):
        super().setUp()
        self.init()
        self.env = mock.patch.dict(os.environ, {
            "JARVIS_MEDIA_WORKER": str(CORE / "tests" / "fixtures" / "fake_media_worker.py"), "FAKE_MEDIA_FAIL": "Bad1"})
        self.env.start()
        self.alerts = mock.patch.object(media, "_alert")
        self.sent = self.alerts.start()
        self.briefs = []

    def tearDown(self):
        self.alerts.stop()
        self.env.stop()
        super().tearDown()

    def worker(self, brief):
        self.briefs.append(brief)
        return {"id": 7}

    def test_watch_saves_a_memory_entry(self):
        entry, folder = media.watch(self.conn, "https://www.instagram.com/reel/Good1/", note="hooks")
        self.assertEqual(folder.name, "Good1")
        self.assertIn("Caption of Good1", memory.get(self.conn, entry.id).body)
        self.assertEqual(media.load_pending(), [])

    def test_watch_deletes_the_video_and_keeps_four_frames(self):
        entry, folder = media.watch(self.conn, "https://www.instagram.com/reel/Good3/")
        self.assertEqual(list(folder.glob("video.*")), [])
        kept = sorted(f.name for f in (folder / "frames").glob("*.jpg"))
        self.assertEqual(kept, ["00-00.jpg", "00-04.jpg", "00-06.jpg", "00-10.jpg"])
        self.assertTrue((folder / "transcript.md").exists())
        body = memory.get(self.conn, entry.id).body
        self.assertIn("Test hook line", body)
        self.assertIn("4 key frames at 00:00, 00:04, 00:06, 00:10", body)
        self.assertEqual(json.loads((folder / "info.json").read_text(encoding="utf-8"))["frames"], 4)

    def test_key_frames_keeps_all_when_there_are_few(self):
        folder = self.home / "few"
        folder.mkdir()
        for name in ("00-00.jpg", "00-02.jpg"):
            (folder / name).write_bytes(b"x")
        self.assertEqual([f.name for f in media.key_frames(folder)], ["00-00.jpg", "00-02.jpg"])

    def test_failed_download_is_queued_once(self):
        url = "https://www.instagram.com/reel/Bad1/?igsh=abc"
        with self.assertRaises(media.DownloadFailed) as cm:
            media.watch(self.conn, url, note="angle ideas")
        self.assertIn("login-only", str(cm.exception))
        self.assertIn("reels-pending.txt", str(cm.exception))
        with self.assertRaises(media.DownloadFailed):
            media.watch(self.conn, "https://www.instagram.com/reel/Bad1/")
        items = media.load_pending()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["note"], "angle ideas")
        self.assertTrue(media.pending_path().read_text(encoding="utf-8").startswith(url + "\t"))

    def test_cookie_file_is_passed_to_the_worker(self):
        media.cookies_path().parent.mkdir(parents=True, exist_ok=True)
        media.cookies_path().write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        _, folder = media.watch(self.conn, "https://www.instagram.com/reel/Good2/")
        args = json.loads((folder / "info.json").read_text(encoding="utf-8"))["args"]
        self.assertEqual(args[args.index("--cookies") + 1], str(media.cookies_path()))

    def test_retry_watches_late_successes_and_reports(self):
        media.pending_path().parent.mkdir(parents=True, exist_ok=True)
        media.pending_path().write_text("https://www.instagram.com/reel/Late1/\n", encoding="utf-8")  # added by hand
        media.add_pending("https://www.instagram.com/reel/Bad1/", note="n")
        rep = media.retry_pending(self.conn, start_worker=self.worker)
        self.assertEqual([w["url"] for w in rep["watched"]], ["https://www.instagram.com/reel/Late1/"])
        self.assertEqual(rep["worker"], 7)
        self.assertIn(rep["watched"][0]["entry"], self.briefs[0])
        self.assertIn("Blake", self.briefs[0])
        left = media.load_pending()
        self.assertEqual([i["url"] for i in left], ["https://www.instagram.com/reel/Bad1/"])
        self.assertEqual(left[0]["tries"], 1)

    def test_retry_backs_off_and_gives_up(self):
        media.add_pending("https://www.instagram.com/reel/Bad1/")
        now = datetime.now()
        media.retry_pending(self.conn, now=now, start_worker=self.worker)
        self.assertFalse(media.retry_due(media.load_pending()[0], now + timedelta(minutes=5)))
        self.assertTrue(media.retry_due(media.load_pending()[0], now + timedelta(minutes=15)))
        items = media.load_pending()
        items[0].update(tries=40, added=(now - timedelta(days=15)).isoformat(timespec="seconds"))
        media.save_pending(items)
        rep = media.retry_pending(self.conn, now=now + timedelta(hours=7), start_worker=self.worker)
        self.assertEqual(len(rep["gave_up"]), 1)
        self.assertEqual(media.load_pending(), [])
        self.assertTrue((paths.work_dir() / "reels-failed.txt").exists())
        self.sent.assert_called_once()
        self.assertEqual(self.briefs, [])

    def test_routine_runs_the_queue(self):
        self.assertIn("reels-retry", [r["name"] for r in routines.routines()])
        self.assertEqual(routines._run_local(self.conn, "reels-retry", datetime.now()), "nothing waiting")
        media.add_pending("https://www.instagram.com/reel/Bad1/")
        self.assertEqual(routines._run_local(self.conn, "reels-retry", datetime.now()), "0 watched, 1 waiting")

    def test_cli_carries_on_after_a_refused_link(self):
        proc = self.run_cli("watch", "https://www.instagram.com/reel/Bad1/", "https://www.instagram.com/reel/Good3/",
                            check=False)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("Queued in", proc.stdout)
        self.assertIn("Saved M-", proc.stdout)
        self.assertIn("1 reel(s) waiting", self.run_cli("watch", "--pending").stdout)
