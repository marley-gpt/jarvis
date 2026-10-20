import base64
import http.client
import json
import threading

from helpers import BrainTest

from jarvis import mentor_capture, paths

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0fakejpeg\xff\xd9").decode()
LESSON = {"course_order": 2, "course_title": "Product Research", "lesson_order": 1,
          "lesson_title": "Deep product Research (Incl Market/Competitor)"}


class MentorCaptureTest(BrainTest):
    def setUp(self):
        super().setUp()
        self.init()
        self.server = mentor_capture.make_server(port=0, token="tok")
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, token="tok", origin="https://www.skool.com", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-Capture-Token"] = token
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = resp.read()
        allow = resp.getheader("Access-Control-Allow-Origin")
        conn.close()
        return resp.status, data, allow

    def test_lesson_transcript_frames_and_images_are_filed(self):
        status, raw, allow = self.request("POST", "/lesson", {**LESSON, "description": "Use these filters",
                                                               "resources": [], "cues": [[0, "All right"], [75, "Filter by"]]})
        self.assertEqual((status, allow), (200, "https://www.skool.com"))
        d = paths.files_dir() / "mentor" / "skool" / "02-product-research" / "01-deep-product-research-incl-market-competitor"
        meta = json.loads((d / "lesson.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["has_transcript"])
        self.assertNotIn("cues", meta)
        transcript = (d / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("[00:00] All right", transcript)
        self.assertIn("[01:15] Filter by", transcript)

        self.assertEqual(self.request("POST", "/frame", {**LESSON, "t": 75.4, "data": "data:image/jpeg;base64," + JPEG})[0], 200)
        self.assertTrue((d / "frames" / "01-15.jpg").read_bytes().startswith(b"\xff\xd8"))
        self.assertEqual(self.request("POST", "/image", {**LESSON, "index": 2, "name": "filters", "data": JPEG})[0], 200)
        self.assertTrue((d / "images" / "02-filters.jpg").exists())

        status, raw, _ = self.request("GET", "/status")
        self.assertEqual(json.loads(raw)["captured"], ["02-product-research/01-deep-product-research-incl-market-competitor"])

    def test_serves_the_capture_script(self):
        status, body, allow = self.request("GET", "/script")
        self.assertEqual((status, allow), (200, "https://www.skool.com"))
        self.assertIn(b"window.JarvisSkool", body)
        self.assertEqual(self.request("GET", "/script", token=None)[0], 403)

    def test_requests_need_token_skool_origin_and_loopback_host(self):
        self.assertEqual(self.request("POST", "/lesson", LESSON, token=None)[0], 403)
        self.assertEqual(self.request("POST", "/lesson", LESSON, origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("POST", "/lesson", LESSON, host="evil.example")[0], 403)
        self.assertEqual(self.request("OPTIONS", "/lesson", origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("OPTIONS", "/lesson")[0], 204)
        self.assertEqual(self.request("POST", "/lesson", {"lesson_title": "x"})[0], 400)

    def test_titles_cannot_escape_the_mentor_folder(self):
        evil = {**LESSON, "course_title": "../../../x", "lesson_title": "..\\..\\y"}
        self.assertEqual(self.request("POST", "/lesson", {**evil, "cues": []})[0], 200)
        files = [p for p in paths.home().rglob("lesson.json")]
        self.assertEqual(len(files), 1)
        self.assertIn(str(paths.files_dir() / "mentor" / "skool"), str(files[0]))


class MentorImportTest(BrainTest):
    def test_bundle_import_files_lesson_and_makes_transcript_searchable(self):
        self.init()
        folder = self.home / "dl"
        folder.mkdir()
        bundle = {"format": "jarvis-skool/1",
                  "lesson": {**LESSON, "url": "https://www.skool.com/x/classroom/y?md=1", "description": "Filters to use",
                             "resources": [{"title": "Sheet", "link": "https://example.com"}],
                             "cues": [[0, "All right"], [75, "always check the Kalodata revenue column"]]},
                  "frames": [{**LESSON, "t": 75, "data": JPEG}], "images": [],
                  "attachments": [{**LESSON, "name": "Testing SOP.txt", "data": base64.b64encode(b"Spend 2x AOV before killing").decode()}]}
        (folder / "jarvis-skool-02-01-deep.json").write_text(json.dumps(bundle), encoding="utf-8")
        (folder / "jarvis-skool-partial.json").write_text("{not json", encoding="utf-8")
        done = mentor_capture.import_bundles(folder, conn=self.conn)
        self.assertEqual(len(done), 1)
        self.assertFalse((folder / "jarvis-skool-02-01-deep.json").exists())
        self.assertTrue((folder / "jarvis-skool-partial.json").exists())
        from jarvis import memory
        hits = memory.search(self.conn, "Kalodata revenue column", category="mentor")
        self.assertEqual(hits[0]["title"], "Transcript: Product Research - Deep product Research (Incl Market/Competitor)")
        self.assertIn("[01:15] always check", memory.get(self.conn, hits[0]["id"]).body)
        self.assertIn("Spend 2x AOV before killing", memory.get(self.conn, hits[0]["id"]).body)
        self.assertTrue(list((self.home / "files").rglob("testing-sop.txt")))
        # Re-importing supersedes instead of duplicating.
        (folder / "jarvis-skool-02-01-deep.json").write_text(json.dumps(bundle), encoding="utf-8")
        mentor_capture.import_bundles(folder, conn=self.conn)
        active = self.conn.execute("SELECT COUNT(*) FROM memory WHERE title LIKE 'Transcript:%' AND status = 'active'").fetchone()[0]
        self.assertEqual(active, 1)
