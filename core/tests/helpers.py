import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent
sys.path.insert(0, str(CORE))

from jarvis import cli, db  # noqa: E402


class BrainTest(unittest.TestCase):
    """Each test gets its own empty JARVIS_HOME."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="jarvis-test-"))
        self._old = os.environ.get("JARVIS_HOME")
        os.environ["JARVIS_HOME"] = str(self.home)
        # The sales watch's connector pull never starts a real Claude run in a test (T103).
        os.environ["JARVIS_SALES_FEED_COMMAND"] = str(CORE / "tests" / "fixtures" / "fake_sales_feed.py")
        # Tests behave the same on the standby laptop as on the primary PC (T105).
        os.environ["JARVIS_ROLE"] = "primary"
        # Brain sync never touches the owner's real OneDrive folder from a test.
        self.sync_dir = Path(tempfile.mkdtemp(prefix="jarvis-sync-"))
        os.environ["JARVIS_SYNC_DIR"] = str(self.sync_dir)
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        if self._old is None:
            os.environ.pop("JARVIS_HOME", None)
        else:
            os.environ["JARVIS_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.sync_dir, ignore_errors=True)

    def init(self):
        cli.cmd_init(self.conn, None)

    def _env(self):
        return dict(os.environ, JARVIS_HOME=str(self.home), PYTHONIOENCODING="utf-8")

    def run_cli(self, *args, stdin=None, check=True):
        """Run the CLI in a fresh process, as a new Claude Code session would."""
        proc = subprocess.run([sys.executable, str(CORE / "jarvis_cli.py"), *args], input=stdin,
                              capture_output=True, text=True, encoding="utf-8", env=self._env(), cwd=REPO)
        if check and proc.returncode != 0:
            raise AssertionError(f"jarvis {' '.join(args)} failed ({proc.returncode}): {proc.stderr}")
        return proc

    def run_hook(self, event, payload):
        return subprocess.run([sys.executable, str(CORE / "jarvis_hook.py"), event], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8", env=self._env(), cwd=REPO)
