"""Tests for agent_runner.py — focused on JSON extraction robustness."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import agent_runner
from llm_client import LLMClientError


# ---------------------------------------------------------------------------
# _extract_agent_json — unit tests
# ---------------------------------------------------------------------------

class TestExtractAgentJson(unittest.TestCase):
    """Tests for the internal _extract_agent_json() helper."""

    def _ok(self, raw: str) -> dict:
        return agent_runner._extract_agent_json(raw, agent_name="analyst")

    def _err(self, raw: str) -> str:
        with self.assertRaises(LLMClientError) as cm:
            agent_runner._extract_agent_json(raw, agent_name="analyst")
        return str(cm.exception)

    # ------------------------------------------------------------------
    # Happy-path: all three parse strategies
    # ------------------------------------------------------------------

    def test_plain_json_parsed(self):
        raw = '{"artifacts": {"analysis": "ok"}, "message": "done"}'
        result = self._ok(raw)
        self.assertEqual(result["message"], "done")

    def test_json_in_backtick_json_fence(self):
        raw = '```json\n{"artifacts": {"analysis": "ok"}, "message": "fenced"}\n```'
        result = self._ok(raw)
        self.assertEqual(result["message"], "fenced")

    def test_json_in_plain_fence(self):
        raw = '```\n{"artifacts": {}, "message": "plain fence"}\n```'
        result = self._ok(raw)
        self.assertEqual(result["message"], "plain fence")

    def test_json_with_preamble_text(self):
        """JSON embedded after a prose preamble is found via brace-scan."""
        raw = 'Here is the analysis result:\n{"artifacts": {}, "message": "embedded"}'
        result = self._ok(raw)
        self.assertEqual(result["message"], "embedded")

    def test_json_with_trailing_text(self):
        raw = '{"artifacts": {}, "message": "ok"}\nSome trailing explanation.'
        result = self._ok(raw)
        self.assertEqual(result["message"], "ok")

    def test_fence_with_leading_text_and_trailing_text(self):
        """Realistic Claude Code output: prose, then fenced JSON, then more prose."""
        raw = (
            "I've completed the analysis.\n\n"
            "```json\n"
            '{"artifacts": {"analysis": "detailed", "acceptance_criteria": ["ac1", "ac2"]}, '
            '"message": "Analysis complete."}\n'
            "```\n\n"
            "Let me know if you need more detail."
        )
        result = self._ok(raw)
        self.assertEqual(result["artifacts"]["analysis"], "detailed")
        self.assertEqual(result["artifacts"]["acceptance_criteria"], ["ac1", "ac2"])

    def test_multiline_json_in_fence(self):
        raw = (
            "```json\n"
            "{\n"
            '  "artifacts": {\n'
            '    "analysis": "multi-line",\n'
            '    "acceptance_criteria": ["a", "b"]\n'
            "  },\n"
            '  "message": "ok"\n'
            "}\n"
            "```"
        )
        result = self._ok(raw)
        self.assertEqual(result["artifacts"]["analysis"], "multi-line")

    def test_nested_objects_in_json(self):
        raw = (
            "```json\n"
            '{"artifacts": {"nested": {"key": "value"}}, "message": "nested"}\n'
            "```"
        )
        result = self._ok(raw)
        self.assertEqual(result["artifacts"]["nested"]["key"], "value")

    def test_json_containing_braces_in_string_values(self):
        """Brace-scan must not be confused by { } inside string values."""
        raw = '{"artifacts": {"analysis": "code: {x = 1}"}, "message": "ok"}'
        result = self._ok(raw)
        self.assertEqual(result["artifacts"]["analysis"], "code: {x = 1}")

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_empty_string_raises_llm_client_error(self):
        msg = self._err("")
        self.assertIn("empty output", msg.lower())

    def test_whitespace_only_raises_llm_client_error(self):
        msg = self._err("   \n\t  ")
        self.assertIn("empty output", msg.lower())

    def test_plain_text_raises_llm_client_error(self):
        msg = self._err("This is just prose, no JSON here.")
        self.assertIn("invalid JSON", msg)

    def test_invalid_json_in_fence_raises(self):
        raw = "```json\nnot valid json\n```"
        msg = self._err(raw)
        self.assertIn("invalid JSON", msg)

    def test_error_includes_agent_name(self):
        with self.assertRaises(LLMClientError) as cm:
            agent_runner._extract_agent_json("bad", agent_name="developer")
        self.assertIn("developer", str(cm.exception))

    def test_error_includes_raw_preview(self):
        raw = "not json output here"
        msg = self._err(raw)
        self.assertIn("not json output here", msg)

    def test_empty_error_mentions_claude_code(self):
        """Empty-output error must mention 'Claude Code' for downstream detection."""
        msg = self._err("")
        self.assertIn("Claude Code", msg)

    def test_array_of_strings_raises(self):
        """A JSON array of scalars has no inner object — must raise."""
        raw = '["item1", "item2", "item3"]'
        msg = self._err(raw)
        self.assertIn("invalid JSON", msg)

    def test_array_wrapping_object_is_tolerated(self):
        """[{...}] — brace-scan extracts the inner object, so no error raised."""
        raw = '[{"artifacts": {"analysis": "x"}, "message": "ok"}]'
        result = self._ok(raw)
        self.assertEqual(result["message"], "ok")

    def test_raw_preview_truncated(self):
        """Very long invalid output should be truncated in the error message."""
        raw = "x" * 2000
        msg = self._err(raw)
        # The preview is capped at 500 chars + surrounding message text
        self.assertLessEqual(len(msg), 700)


# ---------------------------------------------------------------------------
# run_agent integration: fenced JSON from LLM is parsed correctly
# ---------------------------------------------------------------------------

class TestRunAgentFencedJsonIntegration(unittest.TestCase):
    """Ensure run_agent accepts fenced JSON returned by the LLM client."""

    def _make_fenced(self, payload: dict) -> str:
        return "```json\n" + json.dumps(payload) + "\n```"

    def _run(self, raw_output: str) -> dict:
        """Run analyst agent with a fake LLM that returns raw_output."""
        fake_client = MagicMock()
        fake_client.generate.return_value = raw_output
        task = {"id": "TASK-1", "title": "Test task", "status": "refined",
                 "type": "feature", "description": "desc", "artifacts": {},
                 "history": [], "notes": [], "tags": [], "depends_on": [],
                 "blocked_by": [], "blocked_reason": "", "priority": "medium",
                 "estimate": None, "release_id": None, "related_decisions": []}
        with patch("agent_runner.get_llm_client", return_value=fake_client), \
             patch("agent_runner.load_project_context_text", return_value=""), \
             patch("agent_runner.list_project_context_files", return_value=[]), \
             patch("agent_runner.load_decision_context_for_task", return_value=""):
            return agent_runner.run_agent("analyst", task, llm_client=fake_client)

    def test_plain_json_works(self):
        raw = json.dumps({
            "artifacts": {"analysis": "ok", "acceptance_criteria": ["ac1"]},
            "message": "done",
        })
        result = self._run(raw)
        self.assertEqual(result["message"], "done")

    def test_backtick_json_fence_works(self):
        """Core bug fix: ```json fenced output must be accepted."""
        fenced = self._make_fenced({
            "artifacts": {"analysis": "from fence", "acceptance_criteria": ["a"]},
            "message": "fenced output",
        })
        result = self._run(fenced)
        self.assertEqual(result["artifacts"]["analysis"], "from fence")
        self.assertEqual(result["message"], "fenced output")

    def test_plain_fence_works(self):
        payload = json.dumps({
            "artifacts": {"analysis": "plain fence", "acceptance_criteria": []},
            "message": "plain",
        })
        raw = "```\n" + payload + "\n```"
        result = self._run(raw)
        self.assertEqual(result["message"], "plain")

    def test_json_after_preamble_works(self):
        payload = json.dumps({
            "artifacts": {"analysis": "embedded", "acceptance_criteria": []},
            "message": "found it",
        })
        raw = "Sure, here is the JSON:\n" + payload
        result = self._run(raw)
        self.assertEqual(result["message"], "found it")

    def test_artifacts_defaulted_when_missing(self):
        raw = json.dumps({"message": "no artifacts key"})
        result = self._run(raw)
        self.assertIn("artifacts", result)
        self.assertIsInstance(result["artifacts"], dict)

    def test_message_defaulted_when_missing(self):
        raw = json.dumps({"artifacts": {"analysis": "x", "acceptance_criteria": []}})
        result = self._run(raw)
        self.assertIn("message", result)

    def test_empty_output_raises_llm_client_error(self):
        with self.assertRaises(LLMClientError) as cm:
            self._run("")
        self.assertIn("empty output", str(cm.exception).lower())

    def test_invalid_json_raises_llm_client_error(self):
        with self.assertRaises(LLMClientError) as cm:
            self._run("not json at all")
        self.assertIn("invalid JSON", str(cm.exception))

    def test_prompt_source_and_context_files_set(self):
        raw = json.dumps({
            "artifacts": {"analysis": "x", "acceptance_criteria": []},
            "message": "ok",
        })
        result = self._run(raw)
        self.assertIn("prompt_source", result)
        self.assertIn("context_files_used", result)


if __name__ == "__main__":
    unittest.main()
