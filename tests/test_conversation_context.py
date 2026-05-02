import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import decision_log
import orchestrator
import release_manager
import run
import storage
from conversation_context import (
    append_message,
    clear_focus,
    get_focus,
    get_or_create_session,
    load_sessions,
    resolve_reference,
    save_session,
    set_active_decision,
    set_active_release,
    set_active_task,
)


class ConversationContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("tasks", "releases", "decisions", "sessions"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        for folder in ("agents", "project_context", "artifacts", "docs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (self.root / "releases" / "releases.json").write_text("[]", encoding="utf-8")
        (self.root / "decisions" / "index.json").write_text("[]", encoding="utf-8")
        (self.root / "sessions" / "sessions.json").write_text("[]", encoding="utf-8")
        (self.root / "README.md").write_text("readme", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("claude", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("agents", encoding="utf-8")
        for name in ("analyst", "architect", "developer", "qa", "bug_intake", "supervisor"):
            (self.root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

        self.orig_cwd = Path.cwd()
        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_releases = release_manager.RELEASES_PATH
        self.orig_releases_dir = release_manager.RELEASES_DIR
        self.orig_decisions = decision_log.DECISION_INDEX_PATH
        self.orig_decisions_dir = decision_log.DECISIONS_DIR
        self.orig_json_paths = dict(storage.JSON_COLLECTION_PATHS)

        os.chdir(self.root)
        orchestrator.TASKS_PATH = self.root / "tasks" / "tasks.json"
        release_manager.RELEASES_PATH = self.root / "releases" / "releases.json"
        release_manager.RELEASES_DIR = self.root / "releases"
        decision_log.DECISION_INDEX_PATH = self.root / "decisions" / "index.json"
        decision_log.DECISIONS_DIR = self.root / "decisions"

        storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
        storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
        storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH
        storage.JSON_COLLECTION_PATHS["sessions"] = self.root / "sessions" / "sessions.json"

    def tearDown(self):
        os.chdir(self.orig_cwd)
        orchestrator.TASKS_PATH = self.orig_tasks
        release_manager.RELEASES_PATH = self.orig_releases
        release_manager.RELEASES_DIR = self.orig_releases_dir
        decision_log.DECISION_INDEX_PATH = self.orig_decisions
        decision_log.DECISIONS_DIR = self.orig_decisions_dir
        storage.JSON_COLLECTION_PATHS.clear()
        storage.JSON_COLLECTION_PATHS.update(self.orig_json_paths)
        self.tmp.cleanup()

    def test_sessions_collection_exists_in_storage(self):
        with unittest.mock.patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            storage.init_storage()
            info = storage.storage_info()
            self.assertIn("sessions", info["collections"])

    def test_get_or_create_and_save_session(self):
        session = get_or_create_session("cli:default", user_id="u1", channel="cli")
        self.assertEqual(session["session_id"], "cli:default")
        session["last_action"] = "focus"
        save_session(session)
        loaded = load_sessions()
        self.assertEqual(loaded[0]["last_action"], "focus")

    def test_set_focus_and_clear(self):
        task = orchestrator.create_task("A", "B")
        rel = release_manager.create_release("v0.1")
        dec = decision_log.create_decision("T", "C", "D", "X")
        set_active_task("cli:default", task["id"])
        set_active_release("cli:default", rel["id"])
        set_active_decision("cli:default", dec["id"])
        focus = get_focus("cli:default")
        self.assertEqual(focus["active_release_id"], rel["id"])
        clear_focus("cli:default")
        self.assertIsNone(get_focus("cli:default")["active_task_id"])

    def test_append_message_caps_recent_messages(self):
        for idx in range(25):
            append_message("cli:default", "user", f"msg-{idx}")
        session = get_or_create_session("cli:default")
        self.assertEqual(len(session["recent_messages"]), 20)

    def test_resolve_reference(self):
        task = orchestrator.create_task("A", "B")
        set_active_task("cli:default", task["id"])
        session = get_or_create_session("cli:default")
        self.assertEqual(resolve_reference("show TASK-1", session)["task_id"], "TASK-1")
        self.assertEqual(resolve_reference("что по ней?", session)["task_id"], task["id"])
        self.assertIsNone(resolve_reference("что делать", {"active_task_id": None})["task_id"])

    def test_cli_focus_commands(self):
        task = orchestrator.create_task("A", "B")
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_focus_task(SimpleNamespace(id=task["id"]))
        self.assertIn(task["id"], out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_focus(SimpleNamespace())
        self.assertIn(task["id"], out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_clear_focus(SimpleNamespace())
        self.assertIn("active_task_id", out.getvalue())

    def test_doctor_and_storage_info_include_sessions(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_doctor(SimpleNamespace())
        self.assertIn("sessions", out.getvalue().lower())
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_storage_info(SimpleNamespace())
        data = json.loads(out.getvalue())
        self.assertIn("sessions", data["collections"])


if __name__ == "__main__":
    unittest.main()
