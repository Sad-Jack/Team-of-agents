import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import orchestrator
import patch_utils
import project_context_loader
import repo_inspector


class ManagedModeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (self.root / "managed").mkdir(parents=True, exist_ok=True)
        (self.root / "managed" / "README.md").write_text("managed", encoding="utf-8")
        (self.root / "managed" / "code.py").write_text("print('x')\n", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        orchestrator.TASKS_PATH = self.root / "tasks" / "tasks.json"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        self.tmp.cleanup()

    def test_patch_apply_uses_managed_root_by_default(self):
        task = orchestrator.create_task("A", "B")
        tasks = orchestrator.load_tasks()
        t = next(item for item in tasks if item["id"] == task["id"])
        t["artifacts"]["patch_proposal"] = {
            "summary": "x",
            "files": [
                {"file_path": "new.txt", "change_type": "create", "content": "hello"},
            ],
            "unified_diff": "",
            "requires_approval": False,
            "approved": True,
            "applied": False,
            "applied_at": None,
        }
        with patch("patch_utils.resolve_managed_repo_path", return_value=(self.root / "managed").as_posix()):
            result = patch_utils.apply_patch_proposal(t)
        self.assertFalse(result["errors"])
        self.assertTrue((self.root / "managed" / "new.txt").exists())

    def test_patch_apply_rejects_escape(self):
        task = orchestrator.create_task("A", "B")
        tasks = orchestrator.load_tasks()
        t = next(item for item in tasks if item["id"] == task["id"])
        t["artifacts"]["patch_proposal"] = {
            "summary": "x",
            "files": [
                {"file_path": "../escape.txt", "change_type": "create", "content": "hello"},
            ],
            "unified_diff": "",
            "requires_approval": False,
            "approved": True,
            "applied": False,
            "applied_at": None,
        }
        with patch("patch_utils.resolve_managed_repo_path", return_value=(self.root / "managed").as_posix()):
            result = patch_utils.apply_patch_proposal(t)
        self.assertTrue(result["errors"])

    def test_repo_inspector_defaults_to_managed_root(self):
        with patch("repo_inspector.resolve_managed_repo_path", return_value=(self.root / "managed").as_posix()):
            summary = repo_inspector.scan_repository()
        self.assertGreaterEqual(summary["total_files_indexed"], 1)


if __name__ == "__main__":
    unittest.main()
