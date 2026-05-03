import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import decision_log
import orchestrator
import release_manager
import run
import storage


class StorageLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / "releases").mkdir(parents=True, exist_ok=True)
        (self.root / "decisions").mkdir(parents=True, exist_ok=True)
        (self.root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (self.root / "releases" / "releases.json").write_text("[]", encoding="utf-8")
        (self.root / "decisions" / "index.json").write_text("[]", encoding="utf-8")
        (self.root / "agents").mkdir(parents=True, exist_ok=True)
        (self.root / "project_context").mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "README.md").write_text("readme", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("claude", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("agents", encoding="utf-8")
        for name in ("analyst", "architect", "developer", "qa", "bug_intake", "supervisor"):
            (self.root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

        self.orig_cwd = Path.cwd()
        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_rel_path = release_manager.RELEASES_PATH
        self.orig_rel_dir = release_manager.RELEASES_DIR
        self.orig_dec_path = decision_log.DECISION_INDEX_PATH
        self.orig_dec_dir = decision_log.DECISIONS_DIR
        self.orig_json_paths = dict(storage.JSON_COLLECTION_PATHS)

        os.chdir(self.root)
        orchestrator.TASKS_PATH = self.root / "tasks" / "tasks.json"
        release_manager.RELEASES_DIR = self.root / "releases"
        release_manager.RELEASES_PATH = self.root / "releases" / "releases.json"
        decision_log.DECISIONS_DIR = self.root / "decisions"
        decision_log.DECISION_INDEX_PATH = self.root / "decisions" / "index.json"

        storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
        storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
        storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH

    def tearDown(self):
        os.chdir(self.orig_cwd)
        orchestrator.TASKS_PATH = self.orig_tasks
        release_manager.RELEASES_PATH = self.orig_rel_path
        release_manager.RELEASES_DIR = self.orig_rel_dir
        decision_log.DECISION_INDEX_PATH = self.orig_dec_path
        decision_log.DECISIONS_DIR = self.orig_dec_dir
        storage.JSON_COLLECTION_PATHS.clear()
        storage.JSON_COLLECTION_PATHS.update(self.orig_json_paths)
        self.tmp.cleanup()

    def test_get_storage_backend_defaults_json(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(storage.get_storage_backend(), "json")

    def test_invalid_storage_backend_raises(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "bad"}, clear=True):
            with self.assertRaises(storage.StorageError):
                storage.get_storage_backend()

    def test_json_load_save_collection(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            storage.init_storage()
            storage.save_collection("tasks", [{"id": "TASK-1"}])
            data = storage.load_collection("tasks")
            self.assertEqual(data[0]["id"], "TASK-1")

    def test_sqlite_init_and_load_save_collection(self):
        db_path = self.root / "data" / "team.db"
        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            storage.init_storage()
            self.assertTrue(db_path.exists())
            storage.save_collection("tasks", [{"id": "TASK-1"}])
            data = storage.load_collection("tasks")
            self.assertEqual(data[0]["id"], "TASK-1")

    def test_storage_info_returns_counts(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            storage.init_storage()
            storage.save_collection("tasks", [{"id": "A"}, {"id": "B"}])
            info = storage.storage_info()
            self.assertEqual(info["collections"]["tasks"]["count"], 2)

    def test_migrate_json_to_sqlite_and_refuse_overwrite(self):
        db_path = self.root / "data" / "team.db"
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            storage.init_storage()
            storage.save_collection("tasks", [{"id": "TASK-1"}])
            storage.save_collection("releases", [{"id": "REL-1", "name": "r", "description": "", "status": "planned", "created_at": "x", "target_date": None, "tasks": [], "notes": "", "risks": [], "rollback_plan": "", "history": []}])
            storage.save_collection("decisions_index", [{"id": "ADR-1"}])

        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            result = storage.migrate_json_to_sqlite(overwrite=True)
            self.assertEqual(result["counts"]["tasks"], 1)
            with self.assertRaises(storage.StorageError):
                storage.migrate_json_to_sqlite(overwrite=False)

    def test_export_sqlite_to_json_and_refuse_overwrite(self):
        db_path = self.root / "data" / "team.db"
        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            storage.init_storage()
            storage.save_collection("tasks", [{"id": "TASK-1"}])
            storage.save_collection("releases", [])
            storage.save_collection("decisions_index", [])

        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            storage.save_collection("tasks", [{"id": "EXISTING"}])

        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            with self.assertRaises(storage.StorageError):
                storage.export_sqlite_to_json(overwrite=False)
            result = storage.export_sqlite_to_json(overwrite=True)
            self.assertIn("counts", result)

    def test_orchestrator_load_save_json_backend(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            task = orchestrator.create_task("A", "B")
            loaded = orchestrator.load_tasks()
            self.assertTrue(any(item["id"] == task["id"] for item in loaded))

    def test_orchestrator_load_save_sqlite_backend(self):
        db_path = self.root / "data" / "team.db"
        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            task = orchestrator.create_task("A", "B")
            loaded = orchestrator.load_tasks()
            self.assertTrue(any(item["id"] == task["id"] for item in loaded))

    def test_release_manager_and_decisions_with_sqlite(self):
        db_path = self.root / "data" / "team.db"
        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            rel = release_manager.create_release("v0.1")
            self.assertTrue(rel["id"].startswith("REL-"))
            dec = decision_log.create_decision("t", "c", "d", "x")
            self.assertTrue(dec["id"].startswith("ADR-"))

    def test_doctor_includes_storage_section(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_doctor(SimpleNamespace())
            text = out.getvalue()
            self.assertIn("Storage:", text)
            self.assertIn("STORAGE_BACKEND", text)

    def test_storage_info_cli(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_storage_info(SimpleNamespace())
            data = json.loads(out.getvalue())
            self.assertIn("backend", data)

    def test_storage_init_cli(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_storage_init(SimpleNamespace())
            self.assertIn("Storage initialized", out.getvalue())

    def test_migration_cli(self):
        db_path = self.root / "data" / "team.db"
        with patch.dict(os.environ, {"STORAGE_BACKEND": "json"}, clear=True):
            orchestrator.create_task("A", "B")
        with patch.dict(
            os.environ,
            {"STORAGE_BACKEND": "sqlite", "SQLITE_DB_PATH": db_path.as_posix()},
            clear=True,
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_migrate_json_to_sqlite(SimpleNamespace(force=True))
            result = json.loads(out.getvalue())
            self.assertIn("counts", result)


if __name__ == "__main__":
    unittest.main()
