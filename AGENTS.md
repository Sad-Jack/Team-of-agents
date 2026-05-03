# AGENTS.md

## Core Ownership
- `orchestrator.py` owns status transitions and gates.
- `agent_runner.py` owns prompt execution and JSON parsing.
- `llm_client.py` owns provider selection.
- `storage.py` owns persistence backend selection and collection read/write.
- `managed_project.py` owns separation between system root and managed repo root.
- `project_manager.py` owns high-level PM operations for end users.
- `command_runner.py` is the only command execution layer.
- `repo_inspector.py` is the only repository inspection layer.
- `backlog.py` owns dependency/readiness logic.
- `decisions/` is the source of architectural decision memory.
- `release_manager.py` owns release metadata and readiness calculations.
- Supervisor maps natural language to safe explicit actions.
- `project_context/*.md` stores reusable project knowledge.
- `CLAUDE.md` is the project instruction file for Claude Code.

## Command Execution Rules
- LLM must never execute commands directly.
- Only allowlisted commands may run.
- No arbitrary shell execution.
- Always run subprocess with `shell=False`.
- No destructive commands.
- Command outputs must be saved in `command_results`.
- QA may use `command_results` as evidence.
- QA must not claim tests were executed without `command_results` evidence.
- System commands (`python3 run.py ...`) run in system root.
- Project commands run in managed repo root by default.

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
- Telegram integration is interface-only and must route through Supervisor.
- Telegram handlers must not duplicate business logic.
- Telegram owner-only mode is required.
- Risky Telegram actions require explicit confirmation (`/yes`).
- Polling mode only for MVP.
- Telegram token and other secrets must not be printed.
- Telegram voice input is interface-only and must route transcript through Supervisor.
- Voice handlers must reuse shared text-processing flow, not duplicate execution logic.
- Audio conversion must use `ffmpeg` via subprocess with `shell=False`.
- STT must be pluggable and disabled by default.
- Temporary voice files must not be committed.
- High-level PM requests should route through Supervisor + Project Manager layer.

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
- No real Claude Code calls in tests.
- Patch safety and command safety must be covered by tests.
- Repository inspection safety must be covered by tests.
- Voice/STT tests must mock Telegram, ffmpeg and STT CLI calls.
- Before adding new features, run `python3 run.py doctor` and tests.

## Provider Rules
- Preferred real provider is `claude_code`.
- OpenAI provider is optional/legacy and not required for normal usage.
- Default provider for local tests and CI remains `fake`.
- `e2e-demo` must not require paid APIs.
- `e2e-demo` must not require Telegram.
- JSON backend remains default.
- SQLite backend is optional.

## Documentation Rules
- Project documentation is maintained in Russian.
- README and docs examples should use `python3` commands.

## Demo Rules
- Demo commands are intended for local MVP validation.
- `demo-reset` is destructive and must require `--yes`.
- Do not remove user data except by explicit `demo-reset --yes`.

## Storage Rules
- Business logic modules should not read/write JSON files directly.
- Use `storage.load_collection/save_collection` (or wrapper functions like `load_tasks/save_tasks`).
- Do not introduce external DB dependencies.
- Do not implement multi-project support in this step.
- Tests should cover both JSON and SQLite where practical.

## Repository Inspection Rules
- No path traversal.
- Do not read `.env` or files under `.git`.
- No direct filesystem inspection by LLM beyond attached repository context.
- Do not expose hidden sensitive files.
- By default inspection targets managed repo root.
- Never inspect outside managed repo root.

## Managed Project Rules
- Team of Agents may be embedded inside another project.
- `MANAGED_REPO_PATH` defines target project root.
- `repo_inspector.py`, `patch_utils.py`, and project command execution target managed repo by default.
- Never confuse system root and managed repo root.
- Tests should cover both same-root (`.`) and parent-root (`..`) modes.

## Project Manager Rules
- Project Manager is the primary high-level user-facing workflow layer.
- Low-level agents remain internal orchestration tools.
- `project_manager.py` may orchestrate safe multi-step actions.
- Risky actions still require explicit confirmation.
- Task notes are discussion memory and must be validated.
- Documentation for PM flows must stay in Russian.

## Out of Scope
- Database storage
- Web server
- Real image parsing
