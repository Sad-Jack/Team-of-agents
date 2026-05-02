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
from patch_utils import apply_patch_proposal, approve_patch
from repo_inspector import build_repository_context_for_task


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

    @staticmethod
    def default_patch_proposal():
        return {
            "summary": "",
            "files": [],
            "unified_diff": "",
            "requires_approval": True,
            "approved": False,
            "applied": False,
            "applied_at": None,
        }

    @staticmethod
    def default_qa_verification():
        return {
            "verdict": "unknown",
            "summary": "",
            "checked_items": [],
            "failed_checks": [],
            "bugs_found": [],
            "recommended_next_status": "review",
        }

    @staticmethod
    def default_repository_context():
        return {
            "attached": False,
            "scanned_at": None,
            "repo_root": ".",
            "summary": {
                "total_files_indexed": 0,
                "interesting_directories": [],
                "ignored_paths": [],
            },
            "relevant_files": [],
            "search_hits": [],
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
            "depends_on": [],
            "blocked_by": [],
            "blocked_reason": "",
            "tags": [],
            "estimate": None,
            "related_decisions": [],
            "release_id": None,
            "artifacts": {
                "analysis": None,
                "acceptance_criteria": [],
                "architecture": None,
                "technical_risks": [],
                "implementation_guidance": None,
                "implementation": None,
                "implementation_plan": self.default_plan(),
                "patch_proposal": self.default_patch_proposal(),
                "changed_files": [],
                "developer_notes": None,
                "test_cases": [],
                "edge_cases": [],
                "bugs": [],
                "qa_verification": self.default_qa_verification(),
                "command_results": [],
                "repository_context": self.default_repository_context(),
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
    def test_default_task_includes_patch_proposal(self):
        task = orchestrator.create_task("Task A", "Task description")
        self.assertIn("patch_proposal", task["artifacts"])

    def test_validate_accepts_patch_proposal(self):
        orchestrator.validate_task(self.make_task())

    def test_validate_accepts_backlog_fields(self):
        task = self.make_task()
        task["depends_on"] = ["TASK-2"]
        task["blocked_by"] = ["external: pending API"]
        task["blocked_reason"] = "Need API contract."
        task["tags"] = ["backend"]
        task["estimate"] = "medium"
        orchestrator.validate_task(task)

    def test_validate_rejects_self_dependency(self):
        task = self.make_task()
        task["depends_on"] = ["TASK-1"]
        with self.assertRaises(ValueError):
            orchestrator.validate_task(task)

    def test_validate_accepts_repository_context(self):
        task = self.make_task()
        task["artifacts"]["repository_context"] = {
            "attached": True,
            "scanned_at": "2026-05-02T12:00:00Z",
            "repo_root": ".",
            "summary": {
                "total_files_indexed": 3,
                "interesting_directories": ["tasks"],
                "ignored_paths": [".git/config"],
            },
            "relevant_files": [
                {"path": "run.py", "reason": "entrypoint", "size_bytes": 10, "preview": "print"},
            ],
            "search_hits": [
                {"path": "orchestrator.py", "line_number": 1, "line": "import json"},
            ],
        }
        orchestrator.validate_task(task)

    def test_validate_rejects_invalid_repository_context(self):
        task = self.make_task()
        task["artifacts"]["repository_context"] = {"attached": "yes"}
        with self.assertRaises(ValueError):
            orchestrator.validate_task(task)

    def test_validate_rejects_invalid_patch_change_type(self):
        task = self.make_task()
        task["artifacts"]["patch_proposal"]["files"] = [
            {
                "file_path": "a.txt",
                "change_type": "rename",
                "reason": "x",
                "content": "",
                "safe_to_apply": False,
            }
        ]
        with self.assertRaises(ValueError):
            orchestrator.validate_task(task)

    def test_transitions_and_review_gate(self):
        self.assertEqual(orchestrator.run_next_step(self.make_task("idea"), FakeLLMClient())[0]["status"], "refined")
        self.assertEqual(orchestrator.run_next_step(self.make_task("refined"), FakeLLMClient())[0]["status"], "ready_for_dev")
        task, _ = orchestrator.run_next_step(self.make_task("ready_for_dev"), FakeLLMClient())
        self.assertEqual(task["status"], "in_progress")
        self.assertIn("patch_proposal", task["artifacts"])

        review_task = self.make_task("review")
        review_task["artifacts"]["qa_verification"]["verdict"] = "passed"
        self.assertEqual(orchestrator.run_next_step(review_task, FakeLLMClient())[0]["status"], "done")

    def test_run_next_blocked_task_does_not_change(self):
        task = self.make_task("idea")
        task["blocked_by"] = ["external: wait"]
        updated, message = orchestrator.run_next_step(task, FakeLLMClient())
        self.assertEqual(updated["status"], "idea")
        self.assertIn("is blocked", message)


class LLMIntegrationTests(BaseIsolatedTest):
    def test_fake_outputs(self):
        dev = json.loads(FakeLLMClient().generate({"agent_name": "developer", "task": {"title": "T", "description": "D"}}))
        qa = json.loads(FakeLLMClient().generate({"agent_name": "qa", "task": {"title": "T", "description": "D"}}))
        self.assertIn("patch_proposal", dev["artifacts"])
        self.assertIn("qa_verification", qa["artifacts"])

    def test_fake_developer_uses_attached_repository_context(self):
        payload = {
            "agent_name": "developer",
            "task": {
                "title": "T",
                "description": "D",
                "artifacts": {
                    "repository_context": {
                        "attached": True,
                        "relevant_files": [{"path": "orchestrator.py"}],
                    }
                },
            },
        }
        dev = json.loads(FakeLLMClient().generate(payload))
        self.assertEqual(dev["artifacts"]["implementation_plan"]["files_to_modify"][0], "orchestrator.py")

    def test_get_llm_client_openai_missing_key(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""}, clear=True):
            with self.assertRaises(LLMClientError):
                get_llm_client("openai")


class PatchUtilsTests(BaseIsolatedTest):
    def test_apply_patch_refuses_unapproved(self):
        task = self.make_task()
        task["artifacts"]["patch_proposal"]["files"] = [
            {"file_path": "x.txt", "change_type": "create", "reason": "r", "content": "c", "safe_to_apply": False}
        ]
        result = apply_patch_proposal(task, repo_root=self.tmp_dir.name, force=False)
        self.assertTrue(result["errors"])

    def test_apply_patch_create_modify(self):
        task = self.make_task()
        pp = task["artifacts"]["patch_proposal"]
        pp["approved"] = True
        pp["files"] = [
            {"file_path": "new.txt", "change_type": "create", "reason": "r", "content": "one", "safe_to_apply": False},
        ]
        result = apply_patch_proposal(task, repo_root=self.tmp_dir.name, force=False)
        self.assertFalse(result["errors"])
        self.assertTrue((Path(self.tmp_dir.name) / "new.txt").exists())

        task2 = self.make_task()
        pp2 = task2["artifacts"]["patch_proposal"]
        pp2["approved"] = True
        (Path(self.tmp_dir.name) / "mod.txt").write_text("old", encoding="utf-8")
        pp2["files"] = [
            {"file_path": "mod.txt", "change_type": "modify", "reason": "r", "content": "new", "safe_to_apply": False},
        ]
        result2 = apply_patch_proposal(task2, repo_root=self.tmp_dir.name, force=False)
        self.assertFalse(result2["errors"])
        self.assertEqual((Path(self.tmp_dir.name) / "mod.txt").read_text(encoding="utf-8"), "new")

    def test_apply_patch_refuses_unsafe_paths(self):
        for p in ("/abs.txt", "../up.txt", ".env", ".git/config"):
            task = self.make_task()
            pp = task["artifacts"]["patch_proposal"]
            pp["approved"] = True
            pp["files"] = [{"file_path": p, "change_type": "create", "reason": "r", "content": "x", "safe_to_apply": False}]
            result = apply_patch_proposal(task, repo_root=self.tmp_dir.name, force=False)
            self.assertTrue(result["errors"])
            self.assertFalse(task["artifacts"]["patch_proposal"]["applied"])

    def test_approve_patch_sets_flag(self):
        task = self.make_task()
        approve_patch(task)
        self.assertTrue(task["artifacts"]["patch_proposal"]["approved"])


class CLITests(BaseIsolatedTest):
    def _move_to_in_progress(self, task_id: str):
        orchestrator.run_next_for_task(task_id, llm_client=FakeLLMClient())
        orchestrator.run_next_for_task(task_id, llm_client=FakeLLMClient())
        return orchestrator.run_next_for_task(task_id, llm_client=FakeLLMClient())[0]

    def test_patch_cli_and_export(self):
        task = orchestrator.create_task("Task", "Desc")
        self._move_to_in_progress(task["id"])

        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_patch(SimpleNamespace(id=task["id"]))
        self.assertIn("requires_approval", out.getvalue())

        export_path = Path(self.tmp_dir.name) / "patch.md"
        run.cmd_export_patch(SimpleNamespace(id=task["id"], output=str(export_path), force=False))
        self.assertTrue(export_path.exists())
        with self.assertRaises(ValueError):
            run.cmd_export_patch(SimpleNamespace(id=task["id"], output=str(export_path), force=False))
        run.cmd_export_patch(SimpleNamespace(id=task["id"], output=str(export_path), force=True))

    def test_approve_and_apply_patch_cli(self):
        task = orchestrator.create_task("Task", "Desc")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["artifacts"]["patch_proposal"]["files"] = [
            {"file_path": "sample.txt", "change_type": "create", "reason": "r", "content": "hello", "safe_to_apply": False}
        ]
        orchestrator.save_tasks(tasks)

        out1 = io.StringIO()
        with redirect_stdout(out1):
            run.cmd_apply_patch(SimpleNamespace(id=task["id"], force=False))
        self.assertIn("not fully applied", out1.getvalue())

        run.cmd_approve_patch(SimpleNamespace(id=task["id"]))
        out2 = io.StringIO()
        with redirect_stdout(out2):
            run.cmd_apply_patch(SimpleNamespace(id=task["id"], force=False))
        self.assertIn("applied successfully", out2.getvalue())
        self.assertTrue((Path("sample.txt")).exists())
        Path("sample.txt").unlink(missing_ok=True)

    def test_attach_repo_context_cli(self):
        task = orchestrator.create_task("Task", "Desc")
        run.cmd_attach_repo_context(SimpleNamespace(id=task["id"]))
        updated = orchestrator.get_task(task["id"])
        self.assertTrue(updated["artifacts"]["repository_context"]["attached"])

    def test_repo_context_cli_prints(self):
        task = orchestrator.create_task("Task", "Desc")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["artifacts"]["repository_context"] = build_repository_context_for_task(
            target, repo_root=self.tmp_dir.name
        )
        orchestrator.save_tasks(tasks)
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_repo_context(SimpleNamespace(id=task["id"]))
        self.assertIn("attached", out.getvalue())


if __name__ == "__main__":
    unittest.main()
