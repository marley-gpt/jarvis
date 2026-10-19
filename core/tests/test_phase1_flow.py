"""Phase 1 success test: receive a task, delegate it, save results, retrieve them in a later session.

Every step runs the CLI in a separate process, the same way JARVIS and its subagents call it.
"""

from helpers import BrainTest


class Phase1FlowTest(BrainTest):
    def test_task_delegated_saved_and_recalled_later(self):
        # Session 1: JARVIS receives "research this product" and delegates.
        self.run_cli("init")
        self.run_cli("product", "add", "Heated Neck Wrap")
        parent = self.run_cli("task", "add", "Prepare test for heated neck wrap", "-p", "heated-neck-wrap").stdout.strip()
        sub = self.run_cli("task", "add", "Customer research", "--agent", "market-research",
                           "-p", "heated-neck-wrap", "--parent", parent).stdout.strip()

        # The specialist works, saves its deliverable, closes its task and records a finding.
        self.run_cli("task", "start", sub)
        path = self.run_cli("task", "path", sub).stdout.strip()
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Customer research\n\nTop pain: desk workers with neck stiffness after 3pm.\n")
        failed = self.run_cli("task", "done", sub, "--summary", "x", check=False)
        self.assertEqual(failed.returncode, 2)  # a deliverable is mandatory
        self.run_cli("task", "done", sub, "--summary", "Top angle: afternoon desk stiffness", "--deliverable", path)
        self.run_cli("mem", "add", "--title", "Neck wrap buyers are desk workers with afternoon stiffness",
                     "-c", "customer-research", "-p", "heated-neck-wrap", "--provenance", "evidence",
                     "--source", path, "--body", "-", stdin="Seen in 14 Reddit comments and 9 reviews.\n")
        self.run_cli("decide", "Proceed to creative for heated neck wrap", "--why", "strong pain evidence",
                     "-p", "heated-neck-wrap", "--by", "jarvis")

        # Session 2 (fresh process): JARVIS recalls everything without the old context.
        brief = self.run_cli("brief").stdout
        self.assertIn(f"{parent} [todo]", brief)
        self.assertIn("heated-neck-wrap (idea)", brief)
        self.assertIn("Proceed to creative", brief)
        hits = self.run_cli("mem", "search", "neck stiffness desk").stdout
        self.assertIn("afternoon stiffness", hits)
        self.assertIn("customer-research · evidence", hits)
        dossier = self.run_cli("product", "show", "heated-neck-wrap").stdout
        self.assertIn(f"{sub} [done]", dossier)
        self.assertIn("work/heated-neck-wrap/", dossier)
        self.assertIn("status:done", self.run_cli("task", "show", sub).stdout)

    def test_errors_are_clean(self):
        proc = self.run_cli("mem", "add", "--title", "x", "-c", "mentor", "--provenance", "mentor", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("must cite its source", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
