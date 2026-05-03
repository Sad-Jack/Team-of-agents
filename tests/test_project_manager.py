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
from project_manager import (
    add_task_note,
    advance_task_safely,
    get_blockers_summary,
    get_next_work_recommendation,
    get_project_status,
    get_release_summary,
    get_task_status,
    list_task_notes,
    prepare_task_for_development,
    summarize_task_discussion,
)
from release_manager import add_task_to_release, create_release, load_releases, save_releases


class ProjectManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "tasks").mkdir(parents=True, exist_ok=True)
        (root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (root / "releases").mkdir(parents=True, exist_ok=True)
        (root / "releases" / "releases.json").write_text("[]", encoding="utf-8")
        (root / "decisions").mkdir(parents=True, exist_ok=True)
        (root / "decisions" / "index.json").write_text("[]", encoding="utf-8")
        (root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake", "supervisor"):
            (root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")
        (root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (root / "project_context" / filename).write_text("ctx", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_ctx = project_context_loader.PROJECT_CONTEXT_DIR
        import release_manager
        import decision_log

        self.orig_rel_path = release_manager.RELEASES_PATH
        self.orig_rel_dir = release_manager.RELEASES_DIR
        self.orig_dec_idx = decision_log.DECISION_INDEX_PATH
        self.orig_dec_dir = decision_log.DECISIONS_DIR

        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"
        release_manager.RELEASES_PATH = root / "releases" / "releases.json"
        release_manager.RELEASES_DIR = root / "releases"
        decision_log.DECISION_INDEX_PATH = root / "decisions" / "index.json"
        decision_log.DECISIONS_DIR = root / "decisions"

    def tearDown(self):
        import release_manager
        import decision_log

        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_ctx
        release_manager.RELEASES_PATH = self.orig_rel_path
        release_manager.RELEASES_DIR = self.orig_rel_dir
        decision_log.DECISION_INDEX_PATH = self.orig_dec_idx
        decision_log.DECISIONS_DIR = self.orig_dec_dir
        self.tmp.cleanup()

    def test_default_task_has_notes(self):
        task = orchestrator.create_task("A", "B")
        fetched = orchestrator.get_task(task["id"])
        self.assertEqual(fetched["notes"], [])

    def test_add_and_list_notes(self):
        task = orchestrator.create_task("A", "B")
        add_task_note(task["id"], "Проверить edge case")
        notes = list_task_notes(task["id"])
        self.assertEqual(len(notes), 1)
        self.assertIn("edge case", notes[0]["text"])

    def test_summarize_task_discussion(self):
        task = orchestrator.create_task("A", "B")
        add_task_note(task["id"], "Нужно ли покрыть это тестом?")
        summary = summarize_task_discussion(task["id"])
        self.assertEqual(summary["notes_count"], 1)
        self.assertEqual(summary["unresolved_questions_count"], 1)

    def test_prepare_task_for_development(self):
        task = orchestrator.create_task("A", "B")
        result = prepare_task_for_development(task["id"])
        self.assertEqual(result["final_status"], "ready_for_dev")

    def test_prepare_task_for_development_blocked(self):
        task = orchestrator.create_task("A", "B")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["blocked_by"] = ["external"]
        target["blocked_reason"] = "wait"
        orchestrator.save_tasks(tasks)
        result = prepare_task_for_development(task["id"])
        self.assertTrue(result["blocked"])

    def test_advance_task_safely_one_step(self):
        task = orchestrator.create_task("A", "B")
        result = advance_task_safely(task["id"])
        self.assertEqual(result["final_status"], "refined")

    def test_get_project_status_and_next_work(self):
        task = orchestrator.create_task("A", "B")
        prepare_task_for_development(task["id"])
        status = get_project_status()
        self.assertGreaterEqual(status["total_tasks"], 1)
        rec = get_next_work_recommendation()
        self.assertIsNotNone(rec)

    def test_get_blockers_summary(self):
        task = orchestrator.create_task("A", "B")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["blocked_by"] = ["external"]
        target["blocked_reason"] = "wait"
        orchestrator.save_tasks(tasks)
        summary = get_blockers_summary()
        self.assertEqual(summary["blocked_count"], 1)

    def test_get_task_status(self):
        task = orchestrator.create_task("A", "B")
        status = get_task_status(task["id"])
        self.assertIn("artifact_summary", status)

    def test_get_release_summary(self):
        task = orchestrator.create_task("A", "B")
        rel = create_release("v0.1.0")
        tasks = orchestrator.load_tasks()
        releases = load_releases()
        add_task_to_release(tasks, releases, task["id"], rel["id"])
        orchestrator.save_tasks(tasks)
        save_releases(releases)
        summary = get_release_summary(rel["id"])
        self.assertIn("readiness", summary)

    def test_cli_project_manager_commands(self):
        task = orchestrator.create_task("A", "B")
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_project_status(SimpleNamespace())
            run.cmd_task_status(SimpleNamespace(id=task["id"]))
            run.cmd_prepare_task(SimpleNamespace(id=task["id"]))
            run.cmd_add_note(SimpleNamespace(id=task["id"], text="x", author="user"))
            run.cmd_notes(SimpleNamespace(id=task["id"]))
        text = out.getvalue()
        self.assertIn("Статус проекта", text)


if __name__ == "__main__":
    unittest.main()
