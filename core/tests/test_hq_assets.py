"""The HQ's CC0 asset packs: where they live, what the page is told, and that the serving route stays inside the folder."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts the package on the path)
from jarvis import hq_assets, paths


class AssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "assets"
        self.patch = mock.patch.object(hq_assets, "root", lambda: self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_nothing_installed(self):
        self.assertEqual(hq_assets.ready(), {"packs": [], "polyhaven": [], "base": "/hq-assets/"})
        st = hq_assets.status()
        self.assertFalse(st["ready"])
        self.assertFalse(st["packs"]["kenney-nature"]["ready"])
        self.assertEqual(st["packs"]["kenney-nature"]["licence"], "CC0")

    def test_pack_extracts_only_kept_members_and_checks_the_hash(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Models/GLTF format/tree_palm.glb", b"glb")
            z.writestr("Models/FBX format/tree_palm.fbx", b"fbx")
            z.writestr("License.txt", b"CC0")
            z.writestr("Models/GLTF format/../../../evil.glb", b"x")
        data = buf.getvalue()
        import hashlib
        good = dict(hq_assets.PACKS["kenney-nature"], sha256=hashlib.sha256(data).hexdigest())
        with mock.patch.dict(hq_assets.PACKS, {"kenney-nature": good}), mock.patch.object(hq_assets, "_get", lambda url, log=None: data):
            info = hq_assets.fetch_pack("kenney-nature")
        self.assertEqual(info["files"], 2)
        self.assertTrue((self.root / "kenney-nature" / "tree_palm.glb").is_file())
        self.assertFalse((self.root / "kenney-nature" / "tree_palm.fbx").exists())
        self.assertFalse((self.root.parent / "evil.glb").exists())
        self.assertIn("kenney-nature", hq_assets.ready()["packs"])
        # a changed archive is refused
        with mock.patch.object(hq_assets, "_get", lambda url, log=None: data + b"!"):
            with self.assertRaises(ValueError):
                hq_assets.fetch_pack("kenney-nature")

    def test_polyhaven_model_lays_files_out_as_the_gltf_expects(self):
        files = {"https://api.polyhaven.com/files/sofa_02": json.dumps({"gltf": {"1k": {"gltf": {
            "url": "https://dl/sofa_02_1k.gltf", "include": {"textures/sofa_02_diff_1k.jpg": {"url": "https://dl/t.jpg"}, "sofa_02.bin": {"url": "https://dl/b.bin"}}}}}}).encode(),
            "https://dl/sofa_02_1k.gltf": b"{}", "https://dl/t.jpg": b"jpg", "https://dl/b.bin": b"bin"}
        with mock.patch.object(hq_assets, "_get", lambda url, log=None: files[url]):
            info = hq_assets.fetch_polyhaven("sofa_02")
        self.assertEqual(info["files"], 3)
        self.assertTrue((self.root / "polyhaven" / "sofa_02" / "textures" / "sofa_02_diff_1k.jpg").is_file())
        self.assertEqual(hq_assets.ready()["polyhaven"], ["sofa_02"])

    def test_serve_stays_inside_the_folder_and_knows_types(self):
        (self.root / "kenney-nature").mkdir(parents=True)
        (self.root / "kenney-nature" / "tree_palm.glb").write_bytes(b"glb")
        (self.root.parent / "secret.txt").write_text("no")
        self.assertEqual(hq_assets.serve("kenney-nature/tree_palm.glb"), (b"glb", "model/gltf-binary"))
        self.assertIsNone(hq_assets.serve("../secret.txt"))
        self.assertIsNone(hq_assets.serve("kenney-nature/../../secret.txt"))
        self.assertIsNone(hq_assets.serve(""))
        (self.root / "kenney-nature" / "run.exe").write_bytes(b"MZ")
        self.assertIsNone(hq_assets.serve("kenney-nature/run.exe"))

    def test_root_prefers_config_then_media_folder(self):
        self.patch.stop()
        try:
            with mock.patch.object(paths, "config", lambda: {"hq_assets_dir": str(self.root)}):
                self.assertEqual(hq_assets.root(), self.root)
            media = Path(self.tmp.name) / "media"
            media.mkdir()
            with mock.patch.object(paths, "config", lambda: {}), mock.patch.dict(os.environ, {"JARVIS_MEDIA": str(media)}):
                self.assertEqual(hq_assets.root(), media / "hq-assets")
        finally:
            self.patch.start()


if __name__ == "__main__":
    unittest.main()
