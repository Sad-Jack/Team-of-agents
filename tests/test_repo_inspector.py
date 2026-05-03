import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_runner
import orchestrator
import project_context_loader
import repo_inspector


class RecordingLLMClient:
    provider_name = "fake"

    def __init__(self):
        self.last_payload = None

    def generate(self, payload: dict) -> str:
        self.last_payload = payload
        return '{"artifacts":{"analysis":"ok","acceptance_criteria":["a"]},"message":"ok"}'


class RepoInspectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")

        (self.root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake"):
            (self.root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

        (self.root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (self.root / "project_context" / filename).write_text(f"# {filename}\nctx", encoding="utf-8")

        (self.root / "README.md").write_text("hello", encoding="utf-8")
        (self.root / "run.py").write_text("print('x')\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1", encoding="utf-8")
        (self.root / ".git").mkdir(parents=True, exist_ok=True)
        (self.root / ".git" / "config").write_text("cfg", encoding="utf-8")
        (self.root / "pkg").mkdir(parents=True, exist_ok=True)
        (self.root / "pkg" / "module.py").write_text("def x():\n    return 1\n", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_orch = orchestrator.AGENTS_DIR
        self.orig_agents_runner = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        orchestrator.TASKS_PATH = self.root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = self.root / "agents"
        agent_runner.AGENTS_DIR = self.root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = self.root / "project_context"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_orch
        agent_runner.AGENTS_DIR = self.orig_agents_runner
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        self.tmp_dir.cleanup()

    def test_is_safe_repo_path(self):
        self.assertTrue(repo_inspector.is_safe_repo_path("pkg/module.py", repo_root=self.root.as_posix()))
        self.assertFalse(repo_inspector.is_safe_repo_path("/etc/passwd", repo_root=self.root.as_posix()))
        self.assertFalse(repo_inspector.is_safe_repo_path("../x.py", repo_root=self.root.as_posix()))
        self.assertFalse(repo_inspector.is_safe_repo_path(".env", repo_root=self.root.as_posix()))

    def test_scan_tree_read_search(self):
        summary = repo_inspector.scan_repository(repo_root=self.root.as_posix())
        self.assertIn("total_files_indexed", summary)
        tree = repo_inspector.list_repository_tree(repo_root=self.root.as_posix(), max_depth=3)
        self.assertTrue(any("pkg/module.py" in item for item in tree))
        preview = repo_inspector.read_repository_file("pkg/module.py", repo_root=self.root.as_posix())
        self.assertIn("def x()", preview["preview"])
        hits = repo_inspector.search_repository("return 1", repo_root=self.root.as_posix(), max_results=10)
        self.assertTrue(any(hit["path"] == "pkg/module.py" for hit in hits))

    def test_read_repository_file_rejects_unsafe(self):
        with self.assertRaises(ValueError):
            repo_inspector.read_repository_file(".env", repo_root=self.root.as_posix())

    def test_build_repository_context_for_task(self):
        task = orchestrator.create_task("T", "D")
        tasks = orchestrator.load_tasks()
        target = next(t for t in tasks if t["id"] == task["id"])
        target["artifacts"]["implementation_plan"]["files_to_modify"] = ["pkg/module.py"]
        context = repo_inspector.build_repository_context_for_task(target, repo_root=self.root.as_posix())
        self.assertTrue(context["attached"])
        self.assertGreaterEqual(len(context["relevant_files"]), 1)

    def test_defaults_to_managed_repo_root(self):
        with patch("repo_inspector.resolve_managed_repo_path", return_value=self.root.as_posix()):
            summary = repo_inspector.scan_repository()
            self.assertGreaterEqual(summary["total_files_indexed"], 1)

    def test_agent_runner_includes_repository_context_only_when_attached(self):
        task = orchestrator.create_task("T", "D")
        client = RecordingLLMClient()

        # not attached => stripped from payload
        agent_runner.run_agent("analyst", task, llm_client=client)
        artifacts = client.last_payload["task"].get("artifacts", {})
        self.assertNotIn("repository_context", artifacts)

        # attached => included
        task2 = orchestrator.get_task(task["id"])
        task2["artifacts"]["repository_context"] = {
            "attached": True,
            "scanned_at": "2026-05-02T12:00:00Z",
            "repo_root": ".",
            "summary": {"total_files_indexed": 1, "interesting_directories": [], "ignored_paths": []},
            "relevant_files": [{"path": "run.py", "reason": "entry", "size_bytes": 1, "preview": "x"}],
            "search_hits": [],
        }
        agent_runner.run_agent("analyst", task2, llm_client=client)
        artifacts2 = client.last_payload["task"].get("artifacts", {})
        self.assertIn("repository_context", artifacts2)


if __name__ == "__main__":
    unittest.main()
