# AGENTS.md

## Core Ownership
- `orchestrator.py` owns status transitions and gates.
- `agent_runner.py` owns prompt execution and JSON parsing.
- `llm_client.py` owns provider selection.
- `command_runner.py` is the only command execution layer.
- `repo_inspector.py` is the only repository inspection layer.
- `backlog.py` owns dependency/readiness logic.
- `decisions/` is the source of architectural decision memory.
- `release_manager.py` owns release metadata and readiness calculations.
- Supervisor maps natural language to safe explicit actions.
- `project_context/*.md` stores reusable project knowledge.

## Command Execution Rules
- LLM must never execute commands directly.
- Only allowlisted commands may run.
- No arbitrary shell execution.
- Always run subprocess with `shell=False`.
- No destructive commands.
- Command outputs must be saved in `command_results`.
- QA may use `command_results` as evidence.
- QA must not claim tests were executed without `command_results` evidence.

## Workflow Rules
- Statuses: `idea`, `refined`, `ready_for_dev`, `in_progress`, `review`, `done`.
- LLM output must never directly set status.
- All persisted state changes go through `orchestrator.py`.
- Repository inspection is read-only only.
- Agents must use attached `repository_context` when available, instead of guessing file paths.
- Blocked tasks must not progress through workflow.
- Dependency changes are explicit orchestrator/CLI actions only.
- Next-task recommendation must be deterministic.

## Backlog Rules
- LLM may suggest dependencies, but must not directly apply them.
- `depends_on`, `blocked_by`, and `blocked_reason` are orchestration data, not LLM-controlled status signals.

## Decision Log Rules
- Accepted decisions are constraints unless explicitly superseded.
- `task.related_decisions` links tasks to relevant ADR records.
- `agent_runner.py` includes decision context for linked tasks.
- Architect should avoid contradicting accepted ADRs.
- Decisions are created explicitly via CLI/orchestrator actions, never silently by LLM.

## Release Rules
- Releases are delivery packages only; they do not deploy anything.
- Release readiness is deterministic and must not be controlled by LLM.
- Release notes, risks, and rollback plans are generated from stored task artifacts.
- Tasks can be linked to at most one release through `task.release_id`.
- Release commands must preserve task workflow status.
- No deployment automation is in scope.

## Supervisor Rules
- Supervisor must return strict JSON action proposals.
- Supervisor must not execute actions directly through LLM output.
- Risky actions require explicit confirmation.
- No arbitrary shell commands.
- No bypassing orchestrator/workflow rules.
- Telegram integration should use Supervisor later.

## Developer Rules
- Developer may produce `implementation_plan` and `patch_proposal`.
- `patch_proposal` is review-only by default.
- LLM must not mark patches approved/applied.
- No automatic file modification during `run-next`.

## QA Rules
- QA must produce `qa_verification`.
- `review -> done` only if verdict is `passed`.
- `failed`/`needs_rework` returns to `ready_for_dev`.
- `unknown` keeps task in `review`.

## Testing Rules
- Use `unittest`.
- Use fake provider in tests.
- No real OpenAI calls in tests.
- Patch safety and command safety must be covered by tests.
- Repository inspection safety must be covered by tests.

## Repository Inspection Rules
- No path traversal.
- Do not read `.env` or files under `.git`.
- No direct filesystem inspection by LLM beyond attached repository context.
- Do not expose hidden sensitive files.

## Out of Scope
- Telegram integration
- Database storage
- Web server
- Real image parsing
