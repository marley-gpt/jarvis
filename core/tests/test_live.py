import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jarvis import live


class FingerprintTest(unittest.TestCase):
    def test_saving_adding_or_removing_a_file_changes_it(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            page = folder / "hq.html"
            page.write_text("one", encoding="utf-8")
            first = live.fingerprint(folder, "*.*")
            self.assertEqual(first, live.fingerprint(folder, "*.*"))
            os.utime(page, ns=(1, 2))
            saved = live.fingerprint(folder, "*.*")
            self.assertNotEqual(saved, first)
            (folder / "hq.js").write_text("two", encoding="utf-8")
            added = live.fingerprint(folder, "*.*")
            self.assertNotEqual(added, saved)
            (folder / "hq.js").unlink()
            self.assertEqual(live.fingerprint(folder, "*.*"), saved)

    def test_inject_goes_before_the_body_closes_and_only_once(self):
        html = live.inject("<html><body><p>hi</p></body></html>")
        self.assertTrue(html.endswith(live.SCRIPT + "\n</body></html>"))
        self.assertEqual(live.inject(html), html)


class WatcherTest(unittest.TestCase):
    def run_watcher(self, busy=lambda: False, ok=""):
        restarted, published = threading.Event(), []
        prints = iter(["a"] + ["b"] * 1000)
        with mock.patch.object(live, "fingerprint", lambda *a: next(prints)), \
                mock.patch.object(live, "code_ok", lambda: ok), mock.patch.object(live, "SETTLE", 0.05):
            w = live.Watcher(busy, restarted.set, published.append, interval=0.01)
            w.start()
            restarted.wait(0.6)
            w.stop()
        return restarted.is_set(), published

    def test_new_code_restarts_once_jarvis_is_idle(self):
        self.assertEqual(self.run_watcher(), (True, []))

    def test_it_waits_while_jarvis_is_answering(self):
        self.assertEqual(self.run_watcher(busy=lambda: True), (False, []))

    def test_code_that_does_not_import_keeps_the_old_server_and_says_so_once(self):
        restarted, published = self.run_watcher(ok="SyntaxError: invalid syntax")
        self.assertFalse(restarted)
        self.assertEqual(len(published), 1)
        self.assertIn("SyntaxError", published[0]["text"])

    def test_the_running_code_imports(self):
        self.assertEqual(live.code_ok(), "")


if __name__ == "__main__":
    unittest.main()
