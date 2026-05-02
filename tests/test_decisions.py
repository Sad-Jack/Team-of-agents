import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import agent_runner
import decision_log
import orchestrator
import project_context_loader
import run


class RecordingLLMClient:
    provider_name = "fake"

    def __init__(self):
        self.last_payload = None

    def generate(self, payload: dict) -> str:
        self.last_payload = payload
        return '{"artifacts":{"architecture":"ok","technical_risks":[],"implementation_guidance":"ok"},"message":"ok"}'


class DecisionLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)

        (root / "tasks").mkdir(parents=True, exist_ok=True)
        (root / "tasks" / "tasks.json").write_text("[]", encoding="utf-8")
        (root / "agents").mkdir(parents=True, exist_ok=True)
        for name in ("analyst", "architect", "developer", "qa", "bug_intake"):
            (root / "agents" / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")
        (root / "project_context").mkdir(parents=True, exist_ok=True)
        for filename in project_context_loader.PROJECT_CONTEXT_FILES:
            (root / "project_context" / filename).write_text(f"# {filename}\nctx", encoding="utf-8")
        (root / "decisions").mkdir(parents=True, exist_ok=True)
        (root / "decisions" / "index.json").write_text("[]", encoding="utf-8")

        self.orig_tasks = orchestrator.TASKS_PATH
        self.orig_agents_o = orchestrator.AGENTS_DIR
        self.orig_agents_r = agent_runner.AGENTS_DIR
        self.orig_context = project_context_loader.PROJECT_CONTEXT_DIR
        self.orig_decisions_dir = decision_log.DECISIONS_DIR
        self.orig_decision_index = decision_log.DECISION_INDEX_PATH

        orchestrator.TASKS_PATH = root / "tasks" / "tasks.json"
        orchestrator.AGENTS_DIR = root / "agents"
        agent_runner.AGENTS_DIR = root / "agents"
        project_context_loader.PROJECT_CONTEXT_DIR = root / "project_context"
        decision_log.DECISIONS_DIR = root / "decisions"
        decision_log.DECISION_INDEX_PATH = decision_log.DECISIONS_DIR / "index.json"

    def tearDown(self):
        orchestrator.TASKS_PATH = self.orig_tasks
        orchestrator.AGENTS_DIR = self.orig_agents_o
        agent_runner.AGENTS_DIR = self.orig_agents_r
        project_context_loader.PROJECT_CONTEXT_DIR = self.orig_context
        decision_log.DECISIONS_DIR = self.orig_decisions_dir
        decision_log.DECISION_INDEX_PATH = self.orig_decision_index
        self.tmp.cleanup()

    def test_default_task_has_related_decisions(self):
        task = orchestrator.create_task("A", "B")
        self.assertIn("related_decisions", task)
        orchestrator.validate_task(task)

    def test_create_decision_and_index(self):
        d1 = decision_log.create_decision("T1", "C", "D", "Q")
        self.assertEqual(d1["id"], "ADR-001")
        self.assertTrue((decision_log.DECISIONS_DIR / "ADR-001.md").exists())
        d2 = decision_log.create_decision("T2", "C", "D", "Q")
        self.assertEqual(d2["id"], "ADR-002")
        index = decision_log.load_decision_index()
        self.assertEqual(len(index), 2)

    def test_read_decision_file(self):
        d1 = decision_log.create_decision("T1", "C", "D", "Q")
        content = decision_log.read_decision_file(d1["id"])
        self.assertIn("# ADR-001: T1", content)

    def test_link_unlink_decision(self):
        task = orchestrator.create_task("A", "B")
        d1 = decision_log.create_decision("T1", "C", "D", "Q")
        tasks = orchestrator.load_tasks()
        decision_log.link_decision_to_task(tasks, task["id"], d1["id"])
        self.assertIn(d1["id"], tasks[0]["related_decisions"])
        decision_log.unlink_decision_from_task(tasks, task["id"], d1["id"])
        self.assertNotIn(d1["id"], tasks[0]["related_decisions"])
        with self.assertRaises(ValueError):
            decision_log.link_decision_to_task(tasks, task["id"], "ADR-404")

    def test_cli_decision_commands(self):
        task = orchestrator.create_task("A", "B")
        out = io.StringIO()
        with redirect_stdout(out):
            run.cmd_create_decision(
                SimpleNamespace(
                    title="Use JSON storage",
                    context="ctx",
                    decision="dec",
                    consequences="cons",
                    status="accepted",
                    tags="storage,mvp",
                    related_task=[task["id"]],
                )
            )
        self.assertIn("ADR-001", out.getvalue())

        out2 = io.StringIO()
        with redirect_stdout(out2):
            run.cmd_decisions(SimpleNamespace())
        self.assertIn("ADR-001", out2.getvalue())

        out3 = io.StringIO()
        with redirect_stdout(out3):
            run.cmd_decision(SimpleNamespace(id="ADR-001"))
        self.assertIn("Use JSON storage", out3.getvalue())

        out4 = io.StringIO()
        with redirect_stdout(out4):
            run.cmd_task_decisions(SimpleNamespace(id=task["id"]))
        self.assertIn("ADR-001", out4.getvalue())

    def test_agent_runner_includes_decision_context(self):
        task = orchestrator.create_task("A", "B")
        d1 = decision_log.create_decision("Rule", "ctx", "dec", "cons")
        tasks = orchestrator.load_tasks()
        tasks[0]["related_decisions"] = [d1["id"]]
        orchestrator.save_tasks(tasks)
        task = orchestrator.get_task(task["id"])

        client = RecordingLLMClient()
        agent_runner.run_agent("architect", task, llm_client=client)
        self.assertIn("decision_context", client.last_payload)
        self.assertIn("ADR-001", client.last_payload["decision_context"])


if __name__ == "__main__":
    unittest.main()
