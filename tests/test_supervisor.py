import io
import json
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
from supervisor import (
    IMPLEMENTED_EXECUTION_ACTIONS,
    execute_supervisor_action,
    plan_supervisor_action,
    validate_supervisor_output,
)


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

        (root / "releases").mkdir(parents=True, exist_ok=True)
        (root / "releases" / "releases.json").write_text("[]", encoding="utf-8")

        (root / "decisions").mkdir(parents=True, exist_ok=True)
        (root / "decisions" / "index.json").write_text("[]", encoding="utf-8")

        self.orig_cwd = Path.cwd()
        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        import decision_log
        import release_manager

        self.orig_decision_dir = decision_log.DECISIONS_DIR
        self.orig_decision_idx = decision_log.DECISION_INDEX_PATH
        self.orig_releases_dir = release_manager.RELEASES_DIR
        self.orig_releases_path = release_manager.RELEASES_PATH

        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"

        # update module globals used by run/supervisor imported modules
        import decision_log
        import release_manager

        decision_log.DECISIONS_DIR = root / "decisions"
        decision_log.DECISION_INDEX_PATH = root / "decisions" / "index.json"
        release_manager.RELEASES_DIR = root / "releases"
        release_manager.RELEASES_PATH = root / "releases" / "releases.json"

        # repo inspector reads current directory
        import os

        os.chdir(root)

    def tearDown(self):
        import os
        import decision_log
        import release_manager

        os.chdir(self.orig_cwd)
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context

        decision_log.DECISIONS_DIR = self.orig_decision_dir
        decision_log.DECISION_INDEX_PATH = self.orig_decision_idx
        release_manager.RELEASES_DIR = self.orig_releases_dir
        release_manager.RELEASES_PATH = self.orig_releases_path

        self.tmp.cleanup()

    def _plan(self, name: str, args: dict | None = None, requires_confirmation: bool = False) -> dict:
        return {
            "intent": name,
            "confidence": 0.9,
            "requires_confirmation": requires_confirmation,
            "action": {"name": name, "args": args or {}},
            "explanation": "test",
            "warnings": [],
        }

    def test_supervisor_prompt_exists(self):
        self.assertTrue(Path("agents/supervisor.md").exists())

    def test_validate_supervisor_output(self):
        plan = self._plan("create_task", {"title": "A", "description": "B"})
        validate_supervisor_output(plan)
        with self.assertRaises(Exception):
            validate_supervisor_output({})
        with self.assertRaises(Exception):
            bad = dict(plan)
            bad["confidence"] = 2
            validate_supervisor_output(bad)
        with self.assertRaises(Exception):
            bad = self._plan("run_all", {}, requires_confirmation=False)
            validate_supervisor_output(bad)

    def test_fake_supervisor_intents(self):
        bug = plan_supervisor_action("Create bug: login 500 error", llm_client=FakeLLMClient())
        self.assertEqual(bug["action"]["name"], "create_bug")

        task = plan_supervisor_action("Create task to add healthcheck", llm_client=FakeLLMClient())
        self.assertEqual(task["action"]["name"], "create_task")

        nxt = plan_supervisor_action("What should I do next?", llm_client=FakeLLMClient())
        self.assertEqual(nxt["action"]["name"], "next_work")

        show = plan_supervisor_action("show task TASK-1", llm_client=FakeLLMClient())
        self.assertEqual(show["action"]["name"], "show_task")

        repo = plan_supervisor_action("repo search orchestrator", llm_client=FakeLLMClient())
        self.assertEqual(repo["action"]["name"], "repo_search")

        managed = plan_supervisor_action("каким проектом ты управляешь?", llm_client=FakeLLMClient())
        self.assertEqual(managed["action"]["name"], "managed_project")

        managed_check = plan_supervisor_action("проверь целевой проект", llm_client=FakeLLMClient())
        self.assertEqual(managed_check["action"]["name"], "managed_project_check")

        proj = plan_supervisor_action("Дай статус проекта", llm_client=FakeLLMClient())
        self.assertEqual(proj["action"]["name"], "project_status")

        nxt = plan_supervisor_action("Что дальше?", llm_client=FakeLLMClient())
        self.assertEqual(nxt["action"]["name"], "next_work")

        prep = plan_supervisor_action("Подготовь TASK-1 к разработке", llm_client=FakeLLMClient())
        self.assertEqual(prep["action"]["name"], "prepare_task_for_dev")

        note = plan_supervisor_action("Добавь заметку к TASK-1: проверить edge case", llm_client=FakeLLMClient())
        self.assertEqual(note["action"]["name"], "add_task_note")

        cmd = plan_supervisor_action("run command python3 run.py validate", llm_client=FakeLLMClient())
        self.assertEqual(cmd["action"]["name"], "run_command")
        self.assertTrue(cmd["requires_confirmation"])

    def test_plan_includes_session_focus(self):
        task = orchestrator.create_task("A", "B")
        execute_supervisor_action(
            self._plan("set_focus_task", {"id": task["id"]}),
            session_id="cli:default",
            user_id="u1",
            channel="cli",
        )
        plan = plan_supervisor_action("Что по ней?", llm_client=FakeLLMClient(), session_id="cli:default", user_id="u1", channel="cli")
        self.assertIn("action", plan)

    def test_supervisor_actions_cli_includes_implemented(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_supervisor_actions(SimpleNamespace())
        text = out.getvalue()
        self.assertIn("repo_search", text)
        self.assertIn("run_command", text)

    def test_execute_read_only_actions(self):
        out_agents = execute_supervisor_action(self._plan("agents"))
        self.assertTrue(out_agents["executed"])

        out_config = execute_supervisor_action(self._plan("config"))
        self.assertTrue(out_config["executed"])
        self.assertIn("LLM_PROVIDER", out_config["result"])

        out_context = execute_supervisor_action(self._plan("context"))
        self.assertTrue(out_context["executed"])

        out_repo_search = execute_supervisor_action(self._plan("repo_search", {"query": "orchestrator"}))
        self.assertTrue(out_repo_search["executed"])

        out_managed = execute_supervisor_action(self._plan("managed_project"))
        self.assertTrue(out_managed["executed"])

        out_managed_check = execute_supervisor_action(self._plan("managed_project_check"))
        self.assertTrue(out_managed_check["executed"])

        out_project = execute_supervisor_action(self._plan("project_status"))
        self.assertTrue(out_project["executed"])

    def test_execute_release_notes_and_rollback(self):
        task = orchestrator.create_task("A", "B")
        rel = execute_supervisor_action(self._plan("create_release", {"name": "v0.1.0"}))
        rel_id = rel["result"]["id"]

        execute_supervisor_action(self._plan("add_to_release", {"task": task["id"], "release": rel_id}))

        notes = execute_supervisor_action(self._plan("release_notes", {"id": rel_id}))
        self.assertTrue(notes["executed"])

        rollback = execute_supervisor_action(self._plan("rollback_plan", {"id": rel_id}))
        self.assertTrue(rollback["executed"])

    def test_execute_write_actions(self):
        base = orchestrator.create_task("Base", "B")
        task = orchestrator.create_task("A", "B")

        create_dec = execute_supervisor_action(
            self._plan(
                "create_decision",
                {
                    "title": "Use JSON storage",
                    "context": "ctx",
                    "decision": "dec",
                    "consequences": "cons",
                },
            )
        )
        self.assertTrue(create_dec["executed"])

        execute_supervisor_action(self._plan("add_dependency", {"id": task["id"], "depends_on": base["id"]}))
        updated = orchestrator.get_task(task["id"])
        self.assertIn(base["id"], updated["depends_on"])

        execute_supervisor_action(
            self._plan("block_task", {"id": task["id"], "blocked_by": ["external:wait"], "reason": "wait"})
        )
        blocked = orchestrator.get_task(task["id"])
        self.assertTrue(blocked["blocked_by"])

        execute_supervisor_action(self._plan("unblock_task", {"id": task["id"]}))
        unblocked = orchestrator.get_task(task["id"])
        self.assertEqual(unblocked["blocked_by"], [])

        execute_supervisor_action(self._plan("approve_patch", {"id": task["id"]}))
        patched = orchestrator.get_task(task["id"])
        self.assertTrue(patched["artifacts"]["patch_proposal"]["approved"])

    def test_execute_project_manager_write_actions(self):
        task = orchestrator.create_task("A", "B")
        prep = execute_supervisor_action(self._plan("prepare_task_for_dev", {"id": task["id"]}))
        self.assertTrue(prep["executed"])

        note = execute_supervisor_action(
            self._plan("add_task_note", {"id": task["id"], "text": "Проверить edge case"})
        )
        self.assertTrue(note["executed"])

        notes = execute_supervisor_action(self._plan("task_notes", {"id": task["id"]}))
        self.assertTrue(notes["executed"])
        self.assertEqual(len(notes["result"]), 1)

    def test_focus_actions_and_followup_without_id(self):
        task = orchestrator.create_task("A", "B")
        set_focus = execute_supervisor_action(
            self._plan("set_focus_task", {"id": task["id"]}),
            session_id="cli:default",
            user_id="u1",
            channel="cli",
        )
        self.assertTrue(set_focus["executed"])

        add_note = execute_supervisor_action(
            self._plan("add_task_note", {"text": "без id"}),
            session_id="cli:default",
            user_id="u1",
            channel="cli",
        )
        self.assertTrue(add_note["executed"])

        focus = execute_supervisor_action(
            self._plan("focus", {}),
            session_id="cli:default",
            user_id="u1",
            channel="cli",
        )
        self.assertEqual(focus["result"]["active_task_id"], task["id"])

    def test_execute_run_command_confirmation_and_execution(self):
        plan = self._plan(
            "run_command",
            {"command": "python3 run.py validate"},
            requires_confirmation=True,
        )
        with self.assertRaises(Exception):
            execute_supervisor_action(plan, confirmed=False)

        result = execute_supervisor_action(plan, confirmed=True)
        self.assertTrue(result["executed"])
        self.assertIn("success", result["result"])

    def test_execute_set_release_status_requires_confirmation(self):
        rel = execute_supervisor_action(self._plan("create_release", {"name": "v0.1.0"}))
        rel_id = rel["result"]["id"]

        risky_plan = self._plan(
            "set_release_status",
            {"id": rel_id, "status": "ready"},
            requires_confirmation=True,
        )
        with self.assertRaises(Exception):
            execute_supervisor_action(risky_plan, confirmed=False)

        ok = execute_supervisor_action(risky_plan, confirmed=True)
        self.assertTrue(ok["executed"])
        self.assertEqual(ok["result"]["status"], "ready")

    def test_supervise_cli_refusal_and_yes_flow(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_supervise(SimpleNamespace(text="run command python3 run.py validate", execute=True, yes=False))
        self.assertIn("refusal_reason", out.getvalue())

        out_yes = io.StringIO()
        with redirect_stdout(out_yes):
            run.cmd_supervise(SimpleNamespace(text="run command python3 run.py validate", execute=True, yes=True))
        self.assertIn('"executed": true', out_yes.getvalue())


if __name__ == "__main__":
    unittest.main()
