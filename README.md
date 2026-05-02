# Multi-Agent Project Management MVP

## Purpose
This repository provides a deterministic multi-agent workflow for feature and bug work.

## Developer Action Layer
When a task reaches the Developer transition (`ready_for_dev -> in_progress`), the Developer agent now produces a structured `implementation_plan` stored in task artifacts.

The plan is a proposal only. No repository files are modified automatically.

`implementation_plan` fields:
- `summary`
- `files_to_create`
- `files_to_modify`
- `proposed_changes`
- `commands_to_run`
- `tests_to_add`
- `risks`
- `rollback_notes`

Each `proposed_changes` entry includes:
- `file_path`
- `change_type` (`create|modify|delete`)
- `reason`
- `description`
- `safe_to_apply` (defaults to `false`)

## Project Context Layer
Agents receive reusable project context from `project_context/*.md` before producing artifacts.

## Architecture
- `orchestrator.py`: validation, workflow transitions, persistence.
- `agent_runner.py`: prompt + context + schema payload, JSON parsing.
- `llm_client.py`: fake/default and optional OpenAI provider.
- `tasks/tasks.json`: source of truth.

## Commands
```bash
python run.py create --title "..." --description "..."
python run.py create-bug --title "..." --description "..." --raw "..."
python run.py list
python run.py show --id TASK-1
python run.py run-next --id TASK-1
python run.py run-all
python run.py dev-plan --id TASK-1
python run.py export-dev-plan --id TASK-1 --output dev_plan_TASK-1.md
python run.py export-dev-plan --id TASK-1 --force
python run.py validate
python run.py agents
python run.py config
python run.py context
python run.py context --show
python -m unittest discover -s tests
```

## Notes
- `dev-plan` prints stored `implementation_plan`.
- If no plan exists, run developer step first.
- `export-dev-plan` writes markdown plan output.
- Default export path: `artifacts/<TASK-ID>/developer_plan.md`.
- Use `--force` to overwrite.

## LLM Providers
- Default: `fake` (deterministic, used by tests)
- Optional: `openai` via env vars
  - `LLM_PROVIDER=openai`
  - `OPENAI_API_KEY=...`
  - `OPENAI_MODEL=gpt-5.1-mini`

Do not commit `.env` or secrets.

## Current Limitations
- No Telegram integration yet.
- No database yet.
- No web server yet.
- No automatic patch application from LLM output.
- No real image parsing yet.

## Next Steps
- Reviewed patch application flow.
- QA verification layer for proposed plan execution.
- Telegram interface later.
