import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import managed_project
import run


class ManagedProjectTests(unittest.TestCase):
    def test_get_system_root_exists(self):
        root = Path(managed_project.get_system_root())
        self.assertTrue(root.exists())
        self.assertTrue(root.is_dir())

    def test_default_path_is_dot(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(managed_project.get_managed_repo_path(), ".")

    def test_resolve_dot(self):
        with patch.dict("os.environ", {"MANAGED_REPO_PATH": "."}, clear=True):
            resolved = Path(managed_project.resolve_managed_repo_path())
            self.assertTrue(resolved.exists())
            self.assertTrue(resolved.is_dir())

    def test_resolve_parent_embedded_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "my-project"
            team = project / "team-agents"
            team.mkdir(parents=True, exist_ok=True)
            (project / "README.md").write_text("x", encoding="utf-8")

            with patch("managed_project.get_system_root", return_value=team.as_posix()):
                resolved = managed_project.resolve_managed_repo_path("..")
                self.assertEqual(Path(resolved), project.resolve())

    def test_invalid_path_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team = root / "team-agents"
            team.mkdir(parents=True, exist_ok=True)
            with patch("managed_project.get_system_root", return_value=team.as_posix()):
                info = managed_project.validate_managed_repo_path("missing")
                self.assertFalse(info["valid"])
                self.assertTrue(info["errors"])

    def test_cli_managed_project(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_managed_project(SimpleNamespace())
        self.assertIn("Управляемый проект", out.getvalue())

    def test_cli_managed_project_check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_managed_project_check(SimpleNamespace())
        self.assertIn('"valid"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
