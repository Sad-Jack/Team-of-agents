# AGENTS.md

## System Purpose
Maintain a simple, testable multi-agent project workflow manager with deterministic orchestration.

## Core Ownership
- `orchestrator.py` owns workflow state and status transitions.
- `agent_runner.py` owns prompt execution and JSON parsing.
- `llm_client.py` owns provider selection.
- `project_context/*.md` is the source of reusable project knowledge.

## Workflow Rules
- Allowed statuses: `idea`, `refined`, `ready_for_dev`, `in_progress`, `review`, `done`.
- Status workflow belongs only to `orchestrator.py`.
- LLM output must never control status transitions.
- All persisted task changes must go through `orchestrator.py`.

## Developer Action Rules
- Developer agent must produce a structured `implementation_plan`.
- Developer must not claim code changed unless files were actually modified.
- Proposed changes are review proposals only.
- `safe_to_apply` defaults to `false`.
- No direct file writes from LLM output.
- Developer step must not auto-apply repository changes.

## Task Type Rules
- Supported task types: `feature`, `bug`.
- Bug tasks require `severity` and structured `artifacts.bug_report`.
- Bug intake must never invent exact facts.
- Missing bug information must be `unknown`.

## Project Context Rules
- All agents receive project context.
- Do not duplicate long project rules in Python code.
- Update `project_context/*.md` when project rules change.
- Update tests when context loading or schemas change.

## Testing Rules
- Use `unittest`.
- Tests must use fake provider by default.
- No real OpenAI calls in tests.
- Command:
  - `python -m unittest discover -s tests`

## Security Rules
- Never commit secrets.
- Never hardcode API keys.
- Keep `.env` ignored.

## Out of Scope
- Telegram integration
- Database storage
- Web server
- Real image recognition/parsing
