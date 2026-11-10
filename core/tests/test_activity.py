import json
import threading
from datetime import datetime, timedelta

from helpers import BrainTest

from jarvis import activity, dashboard


class ActivityTest(BrainTest):
    def test_add_and_recent(self):
        activity.add(self.conn, "product-research", "started", status="started", task="T4", product="posture")
        activity.add(self.conn, "product-research", "used Write", tool="Write", file="brain/work/p/T4.md")
        rows = activity.recent(self.conn)
        self.assertEqual([r["action"] for r in rows], ["used Write", "started"])
        self.assertEqual(rows[1]["task_id"], 4)
        self.assertEqual(rows[0]["file"], "brain/work/p/T4.md")
        self.assertEqual(len(activity.recent(self.conn, since_id=rows[1]["id"])), 1)
        self.assertEqual(activity.recent(self.conn, agent="shopify"), [])

    def test_rejects_bad_input(self):
        with self.assertRaises(activity.ActivityError):
            activity.add(self.conn, "shopify", "x", status="sleeping")
        with self.assertRaises(activity.ActivityError):
            activity.add(self.conn, "shopify", "  ")
        with self.assertRaises(activity.ActivityError):
            activity.add(self.conn, "shopify", "x", task="nope")

    def test_current_state_per_agent(self):
        activity.add(self.conn, "shopify", "drafting PDP")
        activity.add(self.conn, "meta-ads", "started", status="started")
        activity.add(self.conn, "meta-ads", "finished", status="done")
        activity.add(self.conn, "store-qa", "needs login", status="blocked")
        cur = activity.current(self.conn)
        self.assertEqual(cur["shopify"]["state"], "working")
        self.assertEqual(cur["meta-ads"]["state"], "idle")
        self.assertEqual(cur["store-qa"]["state"], "blocked")
        later = datetime.now().astimezone() + timedelta(hours=1)
        self.assertEqual(activity.current(self.conn, now=later)["shopify"]["state"], "idle")

    def test_tool_target(self):
        self.assertEqual(activity.tool_target({"file_path": "a.md", "content": "x"}), ("a.md", None))
        self.assertEqual(activity.tool_target({"command": "bin/jarvis task list"}), (None, "bin/jarvis task list"))
        self.assertEqual(activity.tool_target(None), (None, None))

    def test_cli(self):
        out = self.run_cli("event", "add", "creative-director", "writing briefs", "--task", "T7", "-p", "posture",
                           "--file", "brain/work/posture/T7.md").stdout
        self.assertIn("logged", out)
        listing = self.run_cli("event", "list").stdout
        self.assertIn("creative-director [working] writing briefs (T7 posture brain/work/posture/T7.md)", listing)
        self.assertEqual(self.run_cli("event", "add", "x", "y", "--task", "zz", check=False).returncode, 2)

    def test_hooks_record_subagent_and_tools(self):
        self.run_hook("subagent", {"hook_event_name": "SubagentStart", "agent_type": "market-research",
                                   "session_id": "s1", "agent_id": "a1"})
        self.run_hook("post_tool", {"tool_name": "Write", "agent_type": "market-research", "session_id": "s1",
                                    "tool_input": {"file_path": "brain/work/x/T2.md", "content": "hi"}})
        self.run_hook("post_tool", {"tool_name": "Read", "session_id": "s1", "tool_input": {"file_path": "CLAUDE.md"}})
        self.run_hook("subagent", {"hook_event_name": "SubagentStop", "agent_type": "market-research",
                                   "session_id": "s1"})
        rows = list(reversed(activity.recent(self.conn)))
        self.assertEqual([(r["agent"], r["status"], r["tool"]) for r in rows],
                         [("market-research", "started", None), ("market-research", "working", "Write"),
                          ("jarvis", "working", "Read"), ("market-research", "done", None)])
        self.assertEqual(rows[1]["file"], "brain/work/x/T2.md")

    def test_hooks_record_collaboration_and_errors(self):
        self.run_hook("post_tool", {"tool_name": "Agent", "session_id": "s1",
                                    "tool_input": {"subagent_type": "creative-director", "description": "briefs"}})
        self.run_hook("post_tool", {"tool_name": "Bash", "agent_type": "shopify", "session_id": "s1",
                                    "tool_input": {"command": "x"}, "tool_response": {"is_error": True}})
        collab, err = list(reversed(activity.recent(self.conn)))
        self.assertEqual((collab["agent"], collab["status"], collab["with_agent"], collab["department"]),
                         ("jarvis", "collaborating", "creative-director", "executive"))
        self.assertEqual((err["status"], err["department"]), ("error", "store"))

    def test_task_records_feed_the_log(self):
        from jarvis import records
        tid = records.task_add(self.conn, "Write 15 statics", agent="creative-director")
        records.task_update(self.conn, tid, status="in_progress")
        records.task_update(self.conn, tid, status="done", summary="ok")
        rows = list(reversed(activity.recent(self.conn)))
        self.assertEqual([r["status"] for r in rows], ["assigned", "working", "done"])
        self.assertTrue(all(r["task_id"] == tid and r["department"] == "creative" for r in rows))
        self.assertEqual(activity.current(self.conn)["creative-director"]["state"], "idle")

    def test_hook_survives_bad_payload(self):
        proc = self.run_hook("post_tool", {"tool_name": "Bash", "tool_input": "not a dict", "agent_type": 5})
        self.assertEqual(proc.returncode, 0)
