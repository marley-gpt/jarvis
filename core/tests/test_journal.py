import json

from helpers import BrainTest

from jarvis import journal, paths


def context(proc) -> str:
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


class JournalTest(BrainTest):
    def test_owner_messages_and_replies_are_captured_across_sessions(self):
        self.init()
        self.run_hook("prompt", {"session_id": "s1", "prompt": "My supplier for the posture corrector is ZenSupply, 9 day shipping"})
        self.run_hook("stop", {"session_id": "s1", "last_assistant_message": "Noted ZenSupply and the 9 day shipping."})

        # A later session: the brief carries the last conversation, recall finds it.
        brief = self.run_hook("session_start", {"session_id": "s2"}).stdout
        self.assertIn("Last conversation", brief)
        self.assertIn("ZenSupply", brief)
        found = self.run_cli("recall", "supplier shipping").stdout
        self.assertIn("ZenSupply", found)
        self.assertIn("Owner", found)

        day = paths.journal_dir() / f"{paths.now()[:10]}.md"
        text = day.read_text(encoding="utf-8")
        self.assertIn("Owner", text)
        self.assertIn("Noted ZenSupply", text)

    def test_only_the_owners_own_words_are_journaled(self):
        self.init()
        notice = self.run_hook("prompt", {"session_id": "s1", "prompt": "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"})
        self.assertEqual(notice.stdout.strip(), "")
        self.run_hook("prompt", {"session_id": "s1", "prompt": '<artifact-view-context artifact="x">\n{"mode":"edit"}\n'
                                 "</artifact-view-context>\n<artifact-content-authored-by-others/>\n\nwatch these reels"})
        rows = [dict(r) for r in journal.recent(self.conn, limit=10)]
        self.assertEqual([(r["role"], r["text"]) for r in rows], [("owner", "watch these reels")])

    def test_auto_recall_injects_memory_and_earlier_conversation(self):
        self.init()
        self.run_cli("mem", "add", "--title", "Winning hook style for posture products", "-c", "creative-inspiration",
                     "--provenance", "user", "--body", "Before/after posture photos beat lifestyle shots.")
        self.run_hook("prompt", {"session_id": "s1", "prompt": "remember the posture corrector costs $6.20 landed"})

        ctx = context(self.run_hook("prompt", {"session_id": "s2", "prompt": "what do we know about the posture corrector?"}))
        self.assertIn("Winning hook style for posture products", ctx)
        self.assertIn("$6.20", ctx)
        self.assertIn("mem add", ctx)  # the save reminder is always there

        # The message being asked never recalls itself, and messages from this session aren't repeated.
        ctx = context(self.run_hook("prompt", {"session_id": "s2", "prompt": "posture corrector again"}))
        self.assertNotIn("what do we know", ctx)

    def test_duplicates_ignored_and_secrets_redacted(self):
        self.assertIsNotNone(journal.record(self.conn, "owner", "hello there", "s1"))
        self.assertIsNone(journal.record(self.conn, "owner", "hello there", "s1"))
        self.assertIsNone(journal.record(self.conn, "owner", "   ", "s1"))
        journal.record(self.conn, "owner", "my key is sk-abcdefghijklmnopqrstuvwx and password: hunter2pass", "s1")
        stored = journal.recent(self.conn, limit=1)[0]["text"]
        self.assertNotIn("sk-abcdef", stored)
        self.assertNotIn("hunter2pass", stored)
        self.assertIn("[redacted]", stored)

    def test_reply_read_from_transcript_when_not_given(self):
        lines = [
            {"type": "user", "message": {"role": "user", "content": "check the store"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking now."},
                                                          {"type": "tool_use", "name": "Bash"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "The store has 12 products."}]}},
        ]
        path = self.home / "t.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        self.assertEqual(journal.last_reply(str(path)), "Checking now.\n\nThe store has 12 products.")
        self.run_hook("stop", {"session_id": "s1", "transcript_path": str(path)})
        self.assertIn("12 products", journal.recent(self.conn, limit=1)[0]["text"])
        self.assertEqual(journal.last_reply(str(self.home / "missing.jsonl")), "")
