import json

from helpers import BrainTest

from jarvis import hooks


def decision(tool, **tool_input):
    out = hooks.guard({"tool_name": tool, "tool_input": tool_input})
    return out["hookSpecificOutput"]["permissionDecision"] if out else None


class GuardTest(BrainTest):
    def test_blocks_deleting_brain_data(self):
        for cmd in ("rm -rf brain/memory", "rm brain/jarvis.db", "mv brain/memory /tmp/x",
                    "cd x && rm -r ./brain", 'rm "brain/memory/mentor/a.md"', "git clean -fdx brain",
                    "find . -name x; rm -rf $CLAUDE_PROJECT_DIR/brain/work",
                    "cat > a <<'EOF'\nhello\nEOF\nrm -rf brain"):
            self.assertEqual(decision("Bash", command=cmd), "deny", cmd)

    def test_allows_normal_commands(self):
        for cmd in ("rm -rf brain/scratch/market-research", "ls brain/memory", "rm electron/main/brain.ts.bak",
                    'bin/jarvis mem add --title "Move fast" --body "rm the old brain idea" -c business',
                    "npm test", "cat brain/work/x/T1.md", "bin/jarvis mem status M-1 archived",
                    "cat > t.py <<'EOF'\nrm -rf brain\nsqlite3 brain/jarvis.db 'DELETE FROM tasks'\nEOF"):
            self.assertIsNone(decision("Bash", command=cmd), cmd)

    def test_blocks_raw_sql_and_db_edits(self):
        self.assertEqual(decision("Bash", command="sqlite3 brain/jarvis.db 'DELETE FROM tasks'"), "deny")
        self.assertEqual(decision("Bash", command="echo x > brain/jarvis.db"), "deny")
        self.assertIsNone(decision("Bash", command="sqlite3 brain/jarvis.db 'select * from tasks'"))
        self.assertEqual(decision("Write", file_path="/x/brain/jarvis.db"), "deny")
        self.assertEqual(decision("Edit", file_path="/x/brain/backups/a.zip"), "deny")
        self.assertIsNone(decision("Write", file_path="/x/brain/work/p/T1-a.md"))

    def test_blocks_committing_brain(self):
        self.assertEqual(decision("Bash", command="git add -f brain/memory"), "deny")
        self.assertIsNone(decision("Bash", command="git add core CLAUDE.md"))

    def test_external_actions_need_approval(self):
        for tool in ("mcp__Shopify__update-product", "mcp__Shopify__graphql_mutation",
                     "mcp__Higgsfield__generate_image", "mcp__Google_Drive__trash_file",
                     "mcp__Shopify__create-discount"):
            self.assertEqual(decision(tool), "ask", tool)
        for tool in ("mcp__Shopify__get-product", "mcp__Shopify__search_products", "mcp__Shopify__graphql_query",
                     "mcp__Google_Drive__read_file_content", "mcp__Higgsfield__balance"):
            self.assertIsNone(decision(tool), tool)

    def test_local_app_tools_run_without_the_gate(self):
        for tool in ("mcp__claude-in-chrome__computer", "mcp__claude-in-chrome__navigate",
                     "mcp__Claude_Browser__computer", "mcp__terminal__run_in_terminal",
                     "mcp__ccd_session__start_session", "mcp__computer-use__left_click"):
            self.assertIsNone(decision(tool), tool)


class HookProcessTest(BrainTest):
    def test_session_start_initialises_and_briefs(self):
        proc = self.run_hook("session_start", {"hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("JARVIS brief", proc.stdout)
        self.assertIn("M-SEED-0001", proc.stdout)
        self.assertTrue(list((self.home / "backups").glob("jarvis-*-daily.zip")))

    def test_guard_process_emits_json(self):
        proc = self.run_hook("guard", {"tool_name": "Bash", "tool_input": {"command": "rm -rf brain"}})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        proc = self.run_hook("guard", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(proc.stdout, "")

    def test_logs_actions_and_agent_runs(self):
        self.run_hook("post_tool", {"tool_name": "mcp__Shopify__update-product", "tool_input": {"id": "1"},
                                    "session_id": "s1"})
        self.run_hook("post_tool", {"tool_name": "mcp__Shopify__get-product", "tool_input": {}})
        self.run_hook("subagent", {"hook_event_name": "SubagentStop", "agent_type": "product-research",
                                   "session_id": "s1"})
        log = self.run_cli("log").stdout
        self.assertIn("update-product", log)
        self.assertNotIn("get-product", log)
        self.assertIn("product-research", self.run_cli("agents").stdout)
