import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import agent_runner
import decision_log
import orchestrator
import project_context_loader
import release_manager
import run


class MVPToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)

        (root / "tasks").mkdir(parents=True, exist_ok=True)
        (root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (root / "releases").mkdir(parents=True, exist_ok=True)
        (root / "releases" / "releases.json").write_text("[]", encoding="utf-8")
        (root / "decisions").mkdir(parents=True, exist_ok=True)
        (root / "decisions" / "index.json").write_text("[]", encoding="utf-8")
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        (root / "docs").mkdir(parents=True, exist_ok=True)

        (root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake", "supervisor"):
            (root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

        (root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (root / "project_context" / filename).write_text(f"# {filename}\nctx", encoding="utf-8")

        (root / "README.md").write_text("readme", encoding="utf-8")
        (root / "CLAUDE.md").write_text("claude", encoding="utf-8")
        (root / "AGENTS.md").write_text("agents", encoding="utf-8")

        self.orig_cwd = Path.cwd()
        self.repo_root = self.orig_cwd
        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_ctx = project_context_loader.PROJECT_CONTEXT_DIR
        self.orig_rel_dir = release_manager.RELEASES_DIR
        self.orig_rel_path = release_manager.RELEASES_PATH
        self.orig_dec_dir = decision_log.DECISIONS_DIR
        self.orig_dec_idx = decision_log.DECISION_INDEX_PATH

        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"
        release_manager.RELEASES_DIR = root / "releases"
        release_manager.RELEASES_PATH = root / "releases" / "releases.json"
        decision_log.DECISIONS_DIR = root / "decisions"
        decision_log.DECISION_INDEX_PATH = root / "decisions" / "index.json"

        import os

        os.chdir(root)

    def tearDown(self):
        import os

        os.chdir(self.orig_cwd)
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_ctx
        release_manager.RELEASES_DIR = self.orig_rel_dir
        release_manager.RELEASES_PATH = self.orig_rel_path
        decision_log.DECISIONS_DIR = self.orig_dec_dir
        decision_log.DECISION_INDEX_PATH = self.orig_dec_idx
        self.tmp.cleanup()

    def test_doctor_passes_on_valid_structure(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_doctor(None)
        text = out.getvalue()
        self.assertIn("Система готова", text)
        self.assertIn("Управляемый проект", text)

    def test_doctor_fails_when_critical_file_missing(self):
        Path("README.md").unlink()
        with self.assertRaises(ValueError):
            run.cmd_doctor(None)

    def test_doctor_fails_when_managed_project_invalid(self):
        with patch("run.validate_managed_repo_path", return_value={
            "system_root": "/x",
            "managed_repo_path": "missing",
            "managed_repo_root": "/x/missing",
            "exists": False,
            "is_directory": False,
            "has_git": False,
            "has_readme": False,
            "sample_entries": [],
            "valid": False,
            "errors": ["Path does not exist"],
            "warnings": [],
        }):
            with self.assertRaises(ValueError):
                run.cmd_doctor(None)

    def test_demo_reset_requires_yes(self):
        with self.assertRaises(ValueError):
            run.cmd_demo_reset(type("A", (), {"yes": False})())

    def test_demo_reset_clears_storage(self):
        orchestrator.save_tasks([orchestrator.create_task("A", "B")])
        release_manager.save_releases([release_manager.create_release("v0.1")])
        decision_log.save_decision_index([{"id": "ADR-001", "title": "t", "status": "accepted", "date": "2026-01-01", "tags": [], "related_tasks": [], "file_path": "decisions/ADR-001.md"}])

        run.cmd_demo_reset(type("A", (), {"yes": True})())

        self.assertEqual(orchestrator.load_tasks(), [])
        self.assertEqual(release_manager.load_releases(), [])
        self.assertEqual(decision_log.load_decision_index(), [])

    def test_demo_seed_creates_valid_data(self):
        run.cmd_demo_seed(None)
        self.assertGreaterEqual(len(orchestrator.load_tasks()), 2)
        self.assertGreaterEqual(len(release_manager.load_releases()), 1)
        self.assertGreaterEqual(len(decision_log.load_decision_index()), 1)
        orchestrator.validate_all_tasks()

    def test_demo_prints_flow(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_demo(None)
        text = out.getvalue()
        self.assertIn("python3 run.py backlog", text)
        self.assertIn("python3 run.py release-notes", text)

    def test_e2e_demo_runs_with_fake(self):
        with patch("run.run_safe_command", return_value={
            "command": "python3 run.py validate",
            "exit_code": 0,
            "success": True,
            "stdout": "ok",
            "stderr": "",
            "started_at": "2026-05-02T12:00:00Z",
            "finished_at": "2026-05-02T12:00:01Z",
            "duration_seconds": 1.0,
            "source": "manual",
            "working_directory": ".",
        }):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_e2e_demo(None)
            self.assertIn("E2E Demo", out.getvalue())

    def test_docs_mention_doctor_and_e2e_demo(self):
        readme = (self.repo_root / "README.md").read_text(encoding="utf-8")
        commands = (self.repo_root / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
        self.assertIn("doctor", readme)
        self.assertIn("e2e-demo", readme)
        self.assertIn("doctor", commands)
        self.assertIn("e2e-demo", commands)


if __name__ == "__main__":
    unittest.main()
