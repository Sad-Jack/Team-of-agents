import io
import json
import os
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
from llm_client import FakeLLMClient, LLMClientError, get_llm_client


class BaseIsolatedTest(unittest.TestCase):
    @staticmethod
    def default_plan():
        return {
            "summary": "",
            "files_to_create": [],
            "files_to_modify": [],
            "proposed_changes": [],
            "commands_to_run": [],
            "tests_to_add": [],
            "risks": [],
            "rollback_notes": "",
        }

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

        self.original_tasks_path = orchestrator.TASKS_PATH
        self.original_orchestrator_agents_dir = orchestrator.AGENTS_DIR
        self.original_runner_agents_dir = agent_runner.AGENTS_DIR
        self.original_context_dir = project_context_loader.PROJECT_CONTEXT_DIR

        orchestrator.TASKS_PATH = self.tasks_path
        orchestrator.AGENTS_DIR = self.agents_dir
        agent_runner.AGENTS_DIR = self.agents_dir
        project_context_loader.PROJECT_CONTEXT_DIR = self.context_dir

    def tearDown(self):
        orchestrator.TASKS_PATH = self.original_tasks_path
        orchestrator.AGENTS_DIR = self.original_orchestrator_agents_dir
        agent_runner.AGENTS_DIR = self.original_runner_agents_dir
        project_context_loader.PROJECT_CONTEXT_DIR = self.original_context_dir
        self.tmp_dir.cleanup()

    def make_task(self, status="idea", task_type="feature"):
        task = {
            "id": "TASK-1",
            "type": task_type,
            "title": "Sample",
            "description": "Desc",
            "status": status,
            "priority": "medium",
            "artifacts": {
                "analysis": None,
                "acceptance_criteria": [],
                "architecture": None,
                "technical_risks": [],
                "implementation_guidance": None,
                "implementation": None,
                "implementation_plan": self.default_plan(),
                "changed_files": [],
                "developer_notes": None,
                "test_cases": [],
                "edge_cases": [],
                "bugs": [],
            },
            "history": [],
        }
        if task_type == "bug":
            task["id"] = "BUG-1"
            task["severity"] = "unknown"
            task["artifacts"]["bug_report"] = {
                "summary": "unknown",
                "environment": "unknown",
                "steps_to_reproduce": [],
                "actual_result": "unknown",
                "expected_result": "unknown",
                "logs": [],
                "attachments": [],
                "suspected_area": "unknown",
                "impact": "unknown",
            }
        return task


class OrchestratorTests(BaseIsolatedTest):
    def test_create_task(self):
        task = orchestrator.create_task("Task A", "Task description")
        self.assertEqual(task["id"], "TASK-1")
        self.assertEqual(task["status"], "idea")
        self.assertEqual(task["type"], "feature")
        self.assertIn("implementation_plan", task["artifacts"])

    def test_validate_correct_task(self):
        orchestrator.validate_task(self.make_task())

    def test_reject_invalid_status(self):
        task = self.make_task(status="broken")
        with self.assertRaises(ValueError):
            orchestrator.validate_task(task)

    def test_validate_rejects_invalid_change_type(self):
        task = self.make_task()
        task["artifacts"]["implementation_plan"]["proposed_changes"] = [
            {
                "file_path": "x.py",
                "change_type": "rename",
                "reason": "r",
                "description": "d",
                "safe_to_apply": False,
            }
        ]
        with self.assertRaises(ValueError):
            orchestrator.validate_task(task)

    def test_idea_to_refined_transition(self):
        task, _ = orchestrator.run_next_step(self.make_task(status="idea"), llm_client=FakeLLMClient())
        self.assertEqual(task["status"], "refined")

    def test_refined_to_ready_for_dev_transition(self):
        task, _ = orchestrator.run_next_step(self.make_task(status="refined"), llm_client=FakeLLMClient())
        self.assertEqual(task["status"], "ready_for_dev")

    def test_ready_for_dev_to_in_progress_transition(self):
        task, _ = orchestrator.run_next_step(
            self.make_task(status="ready_for_dev"), llm_client=FakeLLMClient()
        )
        self.assertEqual(task["status"], "in_progress")
        self.assertTrue(task["artifacts"]["implementation_plan"]["proposed_changes"])

    def test_in_progress_to_review_transition(self):
        task, _ = orchestrator.run_next_step(self.make_task(status="in_progress"), llm_client=FakeLLMClient())
        self.assertEqual(task["status"], "review")

    def test_review_to_done_transition(self):
        task, _ = orchestrator.run_next_step(self.make_task(status="review"), llm_client=FakeLLMClient())
        self.assertEqual(task["status"], "done")

    def test_done_task_does_not_change(self):
        task = self.make_task(status="done")
        before = len(task["history"])
        task, message = orchestrator.run_next_step(task, llm_client=FakeLLMClient())
        self.assertEqual(task["status"], "done")
        self.assertEqual(len(task["history"]), before)
        self.assertIn("already done", message)

    def test_history_item_contains_required_fields(self):
        task, _ = orchestrator.run_next_step(self.make_task(status="idea"), llm_client=FakeLLMClient())
        history_item = task["history"][0]
        for key in (
            "timestamp",
            "agent",
            "previous_status",
            "new_status",
            "message",
            "prompt_source",
            "llm_provider",
            "context_files_used",
        ):
            self.assertIn(key, history_item)

    def test_missing_agent_prompt_raises_clear_error(self):
        (self.agents_dir / "analyst.md").unlink()
        with self.assertRaises(ValueError):
            orchestrator.load_agent_prompt("analyst")

    def test_create_bug_creates_bug_task(self):
        bug = orchestrator.create_bug("Login error", "Login fails with 500", llm_client=FakeLLMClient())
        self.assertTrue(bug["id"].startswith("BUG-"))
        self.assertEqual(bug["type"], "bug")
        self.assertEqual(bug["status"], "idea")

    def test_bug_task_validates_successfully(self):
        orchestrator.validate_task(self.make_task(task_type="bug"))

    def test_missing_bug_report_fails_validation(self):
        bug = self.make_task(task_type="bug")
        del bug["artifacts"]["bug_report"]
        with self.assertRaises(ValueError):
            orchestrator.validate_task(bug)

    def test_existing_feature_without_type_migrates(self):
        legacy = {
            "id": "TASK-1",
            "title": "Legacy",
            "description": "Legacy desc",
            "status": "idea",
            "priority": "medium",
            "artifacts": {
                "analysis": None,
                "acceptance_criteria": [],
                "architecture": None,
                "implementation": None,
                "test_cases": [],
                "bugs": [],
            },
            "history": [],
        }
        self.tasks_path.write_text(json.dumps([legacy]), encoding="utf-8")
        tasks = orchestrator.load_tasks()
        self.assertEqual(tasks[0]["type"], "feature")
        self.assertIn("implementation_plan", tasks[0]["artifacts"])


class LLMIntegrationTests(BaseIsolatedTest):
    def test_fake_llm_client_returns_valid_json(self):
        raw = FakeLLMClient().generate({"agent_name": "analyst", "task": {"title": "T", "description": "D"}})
        self.assertIn('"artifacts"', raw)

    def test_bug_intake_fake_provider_returns_valid_bug_json(self):
        data = json.loads(
            FakeLLMClient().generate({"agent_name": "bug_intake", "task": {"title": "Bug", "description": "Desc"}})
        )
        self.assertIn("bug_report", data["artifacts"])

    def test_developer_fake_output_contains_implementation_plan(self):
        data = json.loads(
            FakeLLMClient().generate({"agent_name": "developer", "task": {"title": "Task", "description": "Desc"}})
        )
        self.assertIn("implementation_plan", data["artifacts"])

    def test_get_llm_client_defaults_to_fake(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_llm_client().provider_name, "fake")

    def test_get_llm_client_fake_explicit(self):
        self.assertEqual(get_llm_client("fake").provider_name, "fake")

    def test_get_llm_client_openai_missing_key(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""}, clear=True):
            with self.assertRaises(LLMClientError):
                get_llm_client("openai")

    def test_run_agent_parses_fake_json_output(self):
        out = agent_runner.run_agent(
            "analyst", {"title": "Task", "description": "Desc", "artifacts": {}}, llm_client=FakeLLMClient()
        )
        self.assertIn("analysis", out["artifacts"])

    def test_run_agent_includes_project_context_in_payload(self):
        class RecordingClient:
            provider_name = "fake"

            def __init__(self):
                self.last_payload = None

            def generate(self, payload: dict) -> str:
                self.last_payload = payload
                return json.dumps({"artifacts": {"analysis": "ok"}, "message": "ok"})

        recorder = RecordingClient()
        agent_runner.run_agent(
            "analyst", {"title": "Task", "description": "Desc", "artifacts": {}}, llm_client=recorder
        )
        self.assertIn("project_context", recorder.last_payload)
        self.assertIn("# project_context/project.md", recorder.last_payload["project_context"])

    def test_orchestrator_ignores_llm_provided_status(self):
        task = self.make_task(status="idea")
        updated, _ = orchestrator.run_next_step(task, llm_client=FakeLLMClient())
        self.assertEqual(updated["status"], "refined")

    def test_cli_config_does_not_print_secret_values(self):
        output = io.StringIO()
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-test", "OPENAI_API_KEY": "super-secret-key"},
            clear=True,
        ):
            with redirect_stdout(output):
                run.cmd_config(None)
        text = output.getvalue()
        self.assertIn("OPENAI_API_KEY_SET=true", text)
        self.assertNotIn("super-secret-key", text)


class CLITests(BaseIsolatedTest):
    def test_create_bug_cli_works(self):
        args = SimpleNamespace(
            title="Login error",
            description="Login fails with 500",
            raw="Logs: NullPointerException",
            raw_file=None,
            priority="high",
            severity="major",
            provider="fake",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            run.cmd_create_bug(args)
        self.assertIn("Created BUG-1", output.getvalue())

    def test_list_includes_task_type(self):
        orchestrator.create_task("Feature one", "Desc")
        output = io.StringIO()
        with redirect_stdout(output):
            run.cmd_list(None)
        self.assertIn("feature", output.getvalue())

    def test_show_includes_bug_report_for_bug_task(self):
        bug = orchestrator.create_bug("Login error", "Login fails with 500", llm_client=FakeLLMClient())
        output = io.StringIO()
        with redirect_stdout(output):
            run.cmd_show(SimpleNamespace(id=bug["id"]))
        self.assertIn("bug_report", output.getvalue())

    def test_context_cli_works(self):
        output = io.StringIO()
        with redirect_stdout(output):
            run.cmd_context(SimpleNamespace(show=False))
        text = output.getvalue()
        self.assertIn("project.md", text)


class ProjectContextTests(BaseIsolatedTest):
    def test_project_context_files_exist_and_load_dict(self):
        data = project_context_loader.load_project_context()
        self.assertIsInstance(data, dict)
        self.assertIn("project.md", data)

    def test_project_context_text_has_headers(self):
        text = project_context_loader.load_project_context_text()
        self.assertIn("# project_context/project.md", text)
        self.assertIn("# project_context/restrictions.md", text)

    def test_missing_context_file_raises(self):
        (self.context_dir / "commands.md").unlink()
        with self.assertRaises(ValueError):
            project_context_loader.load_project_context()


class DeveloperPlanTests(BaseIsolatedTest):
    def _move_to_in_progress(self, task_id: str):
        orchestrator.run_next_for_task(task_id, llm_client=FakeLLMClient())
        orchestrator.run_next_for_task(task_id, llm_client=FakeLLMClient())
        return orchestrator.run_next_for_task(task_id, llm_client=FakeLLMClient())[0]

    def test_developer_step_stores_implementation_plan(self):
        task = orchestrator.create_task("Task A", "Task description")
        updated = self._move_to_in_progress(task["id"])
        self.assertTrue(updated["artifacts"]["implementation_plan"]["proposed_changes"])

    def test_dev_plan_cli_shows_plan(self):
        task = orchestrator.create_task("Task A", "Task description")
        self._move_to_in_progress(task["id"])
        output = io.StringIO()
        with redirect_stdout(output):
            run.cmd_dev_plan(SimpleNamespace(id=task["id"]))
        text = output.getvalue()
        self.assertIn("proposed_changes", text)

    def test_export_dev_plan_creates_markdown_file(self):
        task = orchestrator.create_task("Task A", "Task description")
        self._move_to_in_progress(task["id"])
        output_file = Path(self.tmp_dir.name) / "plan.md"
        run.cmd_export_dev_plan(SimpleNamespace(id=task["id"], output=str(output_file), force=False))
        self.assertTrue(output_file.exists())

    def test_export_dev_plan_no_overwrite_without_force(self):
        task = orchestrator.create_task("Task A", "Task description")
        self._move_to_in_progress(task["id"])
        output_file = Path(self.tmp_dir.name) / "plan.md"
        output_file.write_text("old", encoding="utf-8")
        with self.assertRaises(ValueError):
            run.cmd_export_dev_plan(SimpleNamespace(id=task["id"], output=str(output_file), force=False))

    def test_export_dev_plan_overwrite_with_force(self):
        task = orchestrator.create_task("Task A", "Task description")
        self._move_to_in_progress(task["id"])
        output_file = Path(self.tmp_dir.name) / "plan.md"
        output_file.write_text("old", encoding="utf-8")
        run.cmd_export_dev_plan(SimpleNamespace(id=task["id"], output=str(output_file), force=True))
        self.assertIn("Developer Plan", output_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
