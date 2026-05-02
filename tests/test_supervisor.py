import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import agent_runner
import orchestrator
import project_context_loader
import run
from llm_client import FakeLLMClient
from supervisor import plan_supervisor_action, validate_supervisor_output


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "tasks").mkdir(parents=True, exist_ok=True)
        (root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake", "supervisor"):
            (root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")
        (root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (root / "project_context" / filename).write_text(f"# {filename}\nctx", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        self.tmp.cleanup()

    def test_supervisor_prompt_exists(self):
        self.assertTrue(Path("agents/supervisor.md").exists())

    def test_validate_supervisor_output(self):
        plan = {
            "intent": "create_task",
            "confidence": 0.9,
            "requires_confirmation": False,
            "action": {"name": "create_task", "args": {"title": "A", "description": "B"}},
            "explanation": "ok",
            "warnings": [],
        }
        validate_supervisor_output(plan)
        with self.assertRaises(Exception):
            validate_supervisor_output({})
        with self.assertRaises(Exception):
            bad = dict(plan)
            bad["confidence"] = 2
            validate_supervisor_output(bad)
        with self.assertRaises(Exception):
            bad = dict(plan)
            bad["action"] = {"name": "run_all", "args": {}}
            bad["requires_confirmation"] = False
            validate_supervisor_output(bad)

    def test_fake_supervisor_intents(self):
        bug = plan_supervisor_action("Create bug: login 500 error", llm_client=FakeLLMClient())
        self.assertEqual(bug["action"]["name"], "create_bug")
        task = plan_supervisor_action("Create task to add healthcheck", llm_client=FakeLLMClient())
        self.assertEqual(task["action"]["name"], "create_task")
        nxt = plan_supervisor_action("What should I do next?", llm_client=FakeLLMClient())
        self.assertEqual(nxt["action"]["name"], "next_task")

    def test_supervise_cli_plan_and_execute(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_supervise(SimpleNamespace(text="Create task to add healthcheck command", execute=False, yes=False))
        self.assertIn("create_task", out.getvalue())

        out2 = io.StringIO()
        with redirect_stdout(out2):
            run.cmd_supervise(SimpleNamespace(text="Create task to add healthcheck command", execute=True, yes=False))
        self.assertIn("\"executed\": true", out2.getvalue())

        out3 = io.StringIO()
        with redirect_stdout(out3):
            run.cmd_supervise(
                SimpleNamespace(text="Create bug: login returns 500 with NullPointerException", execute=True, yes=False)
            )
        self.assertIn("create_bug", out3.getvalue())

    def test_supervise_risky_without_yes_refused(self):
        with self.assertRaises(ValueError):
            run.cmd_supervise(SimpleNamespace(text="Run all tasks", execute=True, yes=False))

    def test_supervisor_actions_cli(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_supervisor_actions(SimpleNamespace())
        self.assertIn("create_task", out.getvalue())

    def test_execute_next_task(self):
        orchestrator.create_task("A", "B")
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_supervise(SimpleNamespace(text="What should I do next?", execute=True, yes=False))
        self.assertIn("next_task", out.getvalue())


if __name__ == "__main__":
    unittest.main()
