import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent_runner
import orchestrator
import project_context_loader
import run
from llm_client import ClaudeCodeLLMClient, LLMClientError, get_llm_client


class ClaudeCodeClientTests(unittest.TestCase):
    def test_get_llm_client_claude_code(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "claude_code",
                "CLAUDE_CODE_BINARY": "claude",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "120",
            },
            clear=True,
        ):
            client = get_llm_client("claude_code")
            self.assertIsInstance(client, ClaudeCodeLLMClient)

    def test_claude_code_uses_subprocess_without_shell(self):
        client = ClaudeCodeLLMClient(binary="claude", timeout_seconds=12)
        with patch("llm_client.shutil.which", return_value="/usr/bin/claude"):
            with patch("llm_client.subprocess.run") as mocked:
                mocked.return_value = SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
                out = client.generate({"a": 1})
                self.assertEqual(out, '{"ok":true}')
                self.assertFalse(mocked.call_args.kwargs["shell"])
                self.assertTrue(mocked.call_args.args[0][0].endswith("claude"))

    def test_claude_code_binary_missing(self):
        client = ClaudeCodeLLMClient(binary="missing-claude", timeout_seconds=12)
        with patch("llm_client.shutil.which", return_value=None):
            with self.assertRaises(LLMClientError) as ctx:
                client.generate({"a": 1})
        self.assertIn("binary not found", str(ctx.exception).lower())


class RunConfigAndSmokeTests(unittest.TestCase):
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

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        self.tmp.cleanup()

    def test_config_hides_secrets_and_warns(self):
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "claude_code",
                "CLAUDE_CODE_BINARY": "claude",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "120",
                "ANTHROPIC_API_KEY": "secret-anthropic",
                "OPENAI_API_KEY": "secret-openai",
                "OPENAI_MODEL": "gpt-5.1-mini",
            },
            clear=True,
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_config(SimpleNamespace())
            text = out.getvalue()
            self.assertIn("ANTHROPIC_API_KEY_SET=true", text)
            self.assertIn("OPENAI_API_KEY_SET=true", text)
            self.assertIn("MANAGED_REPO_PATH=", text)
            self.assertIn("WARNING:", text)
            self.assertNotIn("secret-anthropic", text)
            self.assertNotIn("secret-openai", text)

    def test_llm_smoke_with_fake_provider(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "fake"}, clear=True):
            out = io.StringIO()
            with redirect_stdout(out):
                run.cmd_llm_smoke(SimpleNamespace(prompt='Return JSON: {"ok": true}'))
            data = json.loads(out.getvalue())
            self.assertTrue(data["ok"])
            self.assertEqual(data["provider"], "fake")


if __name__ == "__main__":
    unittest.main()
