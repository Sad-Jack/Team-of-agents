import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import agent_runner
import orchestrator
import project_context_loader
import release_manager
import run


class ReleaseLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "tasks").mkdir(parents=True, exist_ok=True)
        (root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (root / "releases").mkdir(parents=True, exist_ok=True)
        (root / "releases" / "releases.json").write_text("[]", encoding="utf-8")
        (root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake"):
            (root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")
        (root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (root / "project_context" / filename).write_text(f"# {filename}\nctx", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        self.orig_releases_dir = release_manager.RELEASES_DIR
        self.orig_releases_path = release_manager.RELEASES_PATH
        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"
        release_manager.RELEASES_DIR = root / "releases"
        release_manager.RELEASES_PATH = release_manager.RELEASES_DIR / "releases.json"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        release_manager.RELEASES_DIR = self.orig_releases_dir
        release_manager.RELEASES_PATH = self.orig_releases_path
        self.tmp.cleanup()

    def test_task_release_id_validation(self):
        task = orchestrator.create_task("A", "B")
        self.assertIn("release_id", task)
        task["release_id"] = "REL-001"
        orchestrator.validate_task(task)

    def test_create_release_and_increment(self):
        r1 = release_manager.create_release("v0.1.0", "d")
        r2 = release_manager.create_release("v0.2.0", "d")
        self.assertEqual(r1["id"], "REL-001")
        self.assertEqual(r2["id"], "REL-002")

    def test_validate_release_status(self):
        rel = release_manager.create_release("v0.1.0", "d")
        release_manager.validate_release(rel)
        rel["status"] = "bad"
        with self.assertRaises(ValueError):
            release_manager.validate_release(rel)

    def test_add_remove_task_to_release(self):
        t = orchestrator.create_task("A", "B")
        rel = release_manager.create_release("v0.1.0", "d")
        tasks = orchestrator.load_tasks()
        releases = release_manager.load_releases()
        release_manager.add_task_to_release(tasks, releases, t["id"], rel["id"])
        release_manager.add_task_to_release(tasks, releases, t["id"], rel["id"])
        self.assertEqual(len(releases[0]["tasks"]), 1)
        self.assertEqual(tasks[0]["release_id"], rel["id"])
        release_manager.remove_task_from_release(tasks, releases, t["id"], rel["id"])
        self.assertEqual(tasks[0]["release_id"], None)

    def test_readiness_and_generators(self):
        t = orchestrator.create_task("A", "B")
        rel = release_manager.create_release("v0.1.0", "d")
        tasks = orchestrator.load_tasks()
        releases = release_manager.load_releases()
        release_manager.add_task_to_release(tasks, releases, t["id"], rel["id"])
        readiness = release_manager.calculate_release_readiness(tasks, releases[0])
        self.assertFalse(readiness["ready"])
        tasks[0]["status"] = "done"
        tasks[0]["artifacts"]["qa_verification"]["verdict"] = "passed"
        readiness2 = release_manager.calculate_release_readiness(tasks, releases[0])
        self.assertTrue(readiness2["ready"])
        self.assertIn(t["id"], readiness2["missing_command_results"])
        notes = release_manager.generate_release_notes(tasks, releases[0])
        self.assertIn("# Release Notes", notes)
        risks = release_manager.generate_release_risks(tasks, releases[0])
        self.assertEqual(len(risks), len(list(dict.fromkeys(risks))))
        rollback = release_manager.generate_rollback_plan(tasks, releases[0])
        self.assertIn("# Rollback Plan", rollback)

    def test_set_release_status(self):
        rel = release_manager.create_release("v0.1.0", "d")
        releases = release_manager.load_releases()
        release_manager.set_release_status(releases, rel["id"], "ready")
        self.assertEqual(releases[0]["status"], "ready")

    def test_release_cli_commands(self):
        task = orchestrator.create_task("A", "B")
        run.cmd_create_release(SimpleNamespace(name="v0.1.0", description="d", target_date=None))
        run.cmd_add_to_release(SimpleNamespace(release="REL-001", task=task["id"]))

        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_releases(SimpleNamespace())
        self.assertIn("REL-001", out.getvalue())

        out2 = io.StringIO()
        with redirect_stdout(out2):
            run.cmd_release(SimpleNamespace(id="REL-001"))
        self.assertIn("Linked tasks", out2.getvalue())

        out3 = io.StringIO()
        with redirect_stdout(out3):
            run.cmd_release_readiness(SimpleNamespace(id="REL-001"))
        self.assertIn("release_id", out3.getvalue())

        out4 = io.StringIO()
        with redirect_stdout(out4):
            run.cmd_release_notes(SimpleNamespace(id="REL-001"))
        self.assertIn("# Release Notes", out4.getvalue())

        out5 = io.StringIO()
        with redirect_stdout(out5):
            run.cmd_rollback_plan(SimpleNamespace(id="REL-001"))
        self.assertIn("# Rollback Plan", out5.getvalue())

    def test_export_release_files(self):
        t = orchestrator.create_task("A", "B")
        run.cmd_create_release(SimpleNamespace(name="v0.1.0", description="d", target_date=None))
        run.cmd_add_to_release(SimpleNamespace(release="REL-001", task=t["id"]))

        notes_file = Path(self.tmp.name) / "notes.md"
        run.cmd_export_release_notes(SimpleNamespace(id="REL-001", output=str(notes_file), force=False))
        self.assertTrue(notes_file.exists())
        with self.assertRaises(ValueError):
            run.cmd_export_release_notes(SimpleNamespace(id="REL-001", output=str(notes_file), force=False))
        run.cmd_export_release_notes(SimpleNamespace(id="REL-001", output=str(notes_file), force=True))

        rollback_file = Path(self.tmp.name) / "rollback.md"
        run.cmd_export_rollback_plan(SimpleNamespace(id="REL-001", output=str(rollback_file), force=False))
        self.assertTrue(rollback_file.exists())


if __name__ == "__main__":
    unittest.main()
