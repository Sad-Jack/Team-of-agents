import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import agent_runner
import backlog
import orchestrator
import project_context_loader
import run


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        root = Path(self.tmp_dir.name)

        (root / "tasks").mkdir(parents=True, exist_ok=True)
        (root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake"):
            (root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")
        (root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (root / "project_context" / filename).write_text(f"# {filename}\ncontext", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_orch = orchestrator.AGENTS_DIR
        self.orig_agents_runner = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_orch
        agent_runner.AGENTS_DIR = self.orig_agents_runner
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        self.tmp_dir.cleanup()

    def test_defaults_include_backlog_fields(self):
        task = orchestrator.create_task("A", "B")
        self.assertIn("depends_on", task)
        self.assertIn("blocked_by", task)
        self.assertIn("blocked_reason", task)
        self.assertIn("tags", task)
        self.assertIn("estimate", task)

    def test_dependencies_and_blocking_logic(self):
        t1 = orchestrator.create_task("Base", "D1")
        t2 = orchestrator.create_task("Dep", "D2")
        tasks = orchestrator.load_tasks()
        backlog.add_dependency(tasks, t2["id"], t1["id"])
        orchestrator.save_tasks(tasks)

        tasks2 = orchestrator.load_tasks()
        dep_task = next(t for t in tasks2 if t["id"] == t2["id"])
        self.assertIn(t1["id"], backlog.get_unresolved_dependencies(dep_task, tasks2))
        self.assertTrue(backlog.is_task_blocked(dep_task, tasks2))

        base_task = next(t for t in tasks2 if t["id"] == t1["id"])
        base_task["status"] = "done"
        self.assertEqual(backlog.get_unresolved_dependencies(dep_task, tasks2), [])

    def test_sort_and_recommend(self):
        t1 = orchestrator.create_task("F low", "d")
        t2 = orchestrator.create_task("B high", "d")
        tasks = orchestrator.load_tasks()
        a = next(t for t in tasks if t["id"] == t1["id"])
        b = next(t for t in tasks if t["id"] == t2["id"])
        a["priority"] = "high"
        b["priority"] = "high"
        b["type"] = "bug"
        orchestrator.save_tasks(tasks)
        tasks = orchestrator.load_tasks()
        sorted_tasks = backlog.sort_backlog(tasks)
        self.assertEqual(sorted_tasks[0]["type"], "bug")
        self.assertEqual(backlog.recommend_next_task(tasks)["id"], sorted_tasks[0]["id"])

    def test_add_remove_dependency_and_errors(self):
        t1 = orchestrator.create_task("A", "a")
        t2 = orchestrator.create_task("B", "b")
        tasks = orchestrator.load_tasks()
        backlog.add_dependency(tasks, t2["id"], t1["id"])
        self.assertIn(t1["id"], next(t for t in tasks if t["id"] == t2["id"])["depends_on"])
        backlog.remove_dependency(tasks, t2["id"], t1["id"])
        self.assertNotIn(t1["id"], next(t for t in tasks if t["id"] == t2["id"])["depends_on"])
        with self.assertRaises(ValueError):
            backlog.add_dependency(tasks, t2["id"], "TASK-404")
        with self.assertRaises(ValueError):
            backlog.add_dependency(tasks, t2["id"], t2["id"])

    def test_block_unblock_commands(self):
        task = orchestrator.create_task("A", "b")
        run.cmd_block(SimpleNamespace(id=task["id"], blocked_by="external:wait", reason="waiting"))
        updated = orchestrator.get_task(task["id"])
        self.assertTrue(updated["blocked_by"])
        self.assertEqual(updated["blocked_reason"], "waiting")
        run.cmd_unblock(SimpleNamespace(id=task["id"]))
        updated2 = orchestrator.get_task(task["id"])
        self.assertEqual(updated2["blocked_by"], [])
        self.assertEqual(updated2["blocked_reason"], "")

    def test_backlog_ready_blocked_next_cli(self):
        t1 = orchestrator.create_task("Base", "d")
        t2 = orchestrator.create_task("Dep", "d")
        run.cmd_add_dependency(SimpleNamespace(id=t2["id"], depends_on=t1["id"]))

        out_backlog = io.StringIO()
        with redirect_stdout(out_backlog):
            run.cmd_backlog(SimpleNamespace())
        self.assertIn("depends_on", out_backlog.getvalue())

        out_ready = io.StringIO()
        with redirect_stdout(out_ready):
            run.cmd_ready(SimpleNamespace())
        self.assertIn(t1["id"], out_ready.getvalue())

        out_blocked = io.StringIO()
        with redirect_stdout(out_blocked):
            run.cmd_blocked(SimpleNamespace())
        self.assertIn(t2["id"], out_blocked.getvalue())

        out_next = io.StringIO()
        with redirect_stdout(out_next):
            run.cmd_next_task(SimpleNamespace())
        self.assertIn(t1["id"], out_next.getvalue())

    def test_run_next_does_not_process_blocked_task(self):
        t1 = orchestrator.create_task("Base", "d")
        t2 = orchestrator.create_task("Dep", "d")
        run.cmd_add_dependency(SimpleNamespace(id=t2["id"], depends_on=t1["id"]))
        task, message = orchestrator.run_next_for_task(t2["id"])
        self.assertEqual(task["status"], "idea")
        self.assertIn("is blocked", message)


if __name__ == "__main__":
    unittest.main()
