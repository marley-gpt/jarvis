import os
import tempfile
from pathlib import Path
from unittest import mock

from helpers import BrainTest

from jarvis import paths, routines, telegram


class MachineRoleTest(BrainTest):
    """T105: a standby laptop never answers the phone alongside the primary PC."""

    def setUp(self):
        super().setUp()
        self.init()
        self.local = Path(tempfile.mkdtemp(prefix="jarvis-local-"))
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)})
        self.env.start()
        os.environ.pop("JARVIS_ROLE", None)

    def tearDown(self):
        self.env.stop()
        os.environ["JARVIS_ROLE"] = "primary"
        super().tearDown()

    def test_primary_by_default(self):
        self.assertEqual(paths.machine_role(), "primary")
        self.assertFalse(paths.standby())

    def test_role_file_is_kept_outside_the_brain(self):
        where = paths.set_machine_role("standby")
        self.assertTrue(paths.standby())
        self.assertNotIn(str(paths.home()), str(where))
        with self.assertRaises(ValueError):
            paths.set_machine_role("both")

    def test_env_overrides_the_file(self):
        paths.set_machine_role("standby")
        with mock.patch.dict(os.environ, {"JARVIS_ROLE": "primary"}):
            self.assertFalse(paths.standby())

    def test_standby_skips_routines_and_the_bot(self):
        paths.set_machine_role("standby")
        self.assertEqual(routines.tick(self.conn, start_worker=lambda *a, **k: None), [])
        with mock.patch.object(telegram, "settings", return_value={"bot_token": "x"}):
            self.assertFalse(telegram.Bridge.enabled(object.__new__(telegram.Bridge)))

    def test_bundle_leaves_venvs_videos_and_secrets_out(self):
        import zipfile
        from jarvis import backup
        for rel in ("files/reels/a/clip.mp4", "files/reels/a/transcript.md", "tts/venv/x.py", "tts/models/k.onnx",
                    "secrets/telegram.json", "skills/blake/SKILL.md"):
            p = paths.home() / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        made = backup.bundle(self.conn, self.local / "out", with_secrets=True)
        names = set(zipfile.ZipFile(made[0]).namelist())
        self.assertIn("jarvis.db", names)
        self.assertIn("files/reels/a/transcript.md", names)
        self.assertIn("skills/blake/SKILL.md", names)
        for gone in ("files/reels/a/clip.mp4", "tts/venv/x.py", "tts/models/k.onnx", "secrets/telegram.json"):
            self.assertNotIn(gone, names)
        self.assertEqual(zipfile.ZipFile(made[1]).namelist(), ["secrets/telegram.json"])

    def test_cli_sets_and_shows_the_role(self):
        env = dict(self._env(), LOCALAPPDATA=str(self.local))
        env.pop("JARVIS_ROLE", None)
        with mock.patch.object(self, "_env", return_value=env):
            self.assertIn("standby", self.run_cli("machine", "standby").stdout)
            self.assertIn("Role: standby", self.run_cli("machine").stdout)
