import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent_runner
import orchestrator
import project_context_loader
import run
from command_runner import is_command_allowed, run_safe_command
from llm_client import FakeLLMClient


class CommandLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)

        self.tasks_dir = root / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path = self.tasks_dir / "tasks.json"
        self.tasks_path.write_text("[]", encoding="utf-8")

        self.agents_dir = root / "agents"
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake"):
            (self.agents_dir / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

        self.context_dir = root / "project_context"
        self.context_dir.mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (self.context_dir / filename).write_text(f"# {filename}\ncontext", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_orch = orchestrator.AGENTS_DIR
        self.orig_agents_runner = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        orchestrator.TASKS_PATH = self.tasks_path
        orchestrator.AGENTS_DIR = self.agents_dir
        agent_runner.AGENTS_DIR = self.agents_dir
        project_context_loader.PROJECT_CONTEXT_DIR = self.context_dir

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_orch
        agent_runner.AGENTS_DIR = self.orig_agents_runner
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        self.tmp_dir.cleanup()

    def test_command_allowlist_and_operators(self):
        self.assertTrue(is_command_allowed("python run.py validate"))
        self.assertFalse(is_command_allowed("python run.py validate && whoami"))
        self.assertFalse(is_command_allowed("python run.py unknown"))

    def test_run_safe_command_rejects_disallowed(self):
        with self.assertRaises(ValueError):
            run_safe_command("echo hi")

    def test_run_safe_command_no_shell_true(self):
        with patch("command_runner.subprocess.run") as mocked:
            mocked.return_value = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            result = run_safe_command("python run.py validate")
            self.assertTrue(result["success"])
            self.assertIn("shell", mocked.call_args.kwargs)
            self.assertFalse(mocked.call_args.kwargs["shell"])

    def test_run_safe_command_timeout(self):
        import subprocess

        with patch("command_runner.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
            result = run_safe_command("python run.py validate", timeout_seconds=1)
            self.assertFalse(result["success"])
            self.assertIsNone(result["exit_code"])

    def test_run_command_cli_appends_result(self):
        task = orchestrator.create_task("A", "B")
        fake_result = {
            "command": "python run.py validate",
            "exit_code": 0,
            "success": True,
            "stdout": "ok",
            "stderr": "",
            "started_at": "2026-05-02T12:00:00Z",
            "finished_at": "2026-05-02T12:00:01Z",
            "duration_seconds": 1.0,
            "source": "manual",
            "working_directory": ".",
        }
        with patch("run.run_safe_command", return_value=fake_result):
            run.cmd_run_command(SimpleNamespace(id=task["id"], command="python run.py validate", timeout=30))
        updated = orchestrator.get_task(task["id"])
        self.assertEqual(len(updated["artifacts"]["command_results"]), 1)

    def test_run_plan_commands_runs_allowed_and_skips_disallowed(self):
        task = orchestrator.create_task("A", "B")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["artifacts"]["implementation_plan"]["commands_to_run"] = [
            "python run.py validate",
            "echo blocked",
        ]
        orchestrator.save_tasks(tasks)

        fake_result = {
            "command": "python run.py validate",
            "exit_code": 0,
            "success": True,
            "stdout": "ok",
            "stderr": "",
            "started_at": "2026-05-02T12:00:00Z",
            "finished_at": "2026-05-02T12:00:01Z",
            "duration_seconds": 1.0,
            "source": "implementation_plan",
            "working_directory": ".",
        }
        with patch("run.run_safe_command", return_value=fake_result):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_run_plan_commands(SimpleNamespace(id=task["id"], timeout=30))
            self.assertIn("echo blocked", out.getvalue())

    def test_command_results_cli_prints(self):
        task = orchestrator.create_task("A", "B")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["artifacts"]["command_results"].append(
            {
                "command": "python run.py validate",
                "exit_code": 0,
                "success": True,
                "stdout": "ok",
                "stderr": "",
                "started_at": "2026-05-02T12:00:00Z",
                "finished_at": "2026-05-02T12:00:01Z",
                "duration_seconds": 1.0,
                "source": "manual",
                "working_directory": ".",
            }
        )
        orchestrator.save_tasks(tasks)
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_command_results(SimpleNamespace(id=task["id"]))
        self.assertIn("command=python run.py validate", out.getvalue())

    def test_fake_qa_verdict_passed_or_failed_by_command_results(self):
        payload = {"agent_name": "qa", "task": {"artifacts": {"command_results": [
            {"command": "python -m unittest discover -s tests", "success": True}
        ]}}}
        out = json.loads(FakeLLMClient().generate(payload))
        self.assertEqual(out["artifacts"]["qa_verification"]["verdict"], "passed")

        payload2 = {"agent_name": "qa", "task": {"artifacts": {"command_results": [
            {"command": "python run.py validate", "success": False}
        ]}}}
        out2 = json.loads(FakeLLMClient().generate(payload2))
        self.assertEqual(out2["artifacts"]["qa_verification"]["verdict"], "failed")


if __name__ == "__main__":
    unittest.main()
