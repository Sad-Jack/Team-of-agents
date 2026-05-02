# Multi-Agent Project Management MVP

## Purpose
Deterministic multi-agent workflow for feature and bug tasks with orchestrator-owned transitions.

## Backlog & Dependencies Layer
Tasks include backlog coordination fields:
- `depends_on`: task IDs that must be done first
- `blocked_by`: explicit blockers (task IDs or external references)
- `blocked_reason`: human-readable blocker reason
- `tags`: classification labels
- `estimate`: optional effort estimate (`null`, number, or string)

Blocked/ready logic:
- task is blocked if `blocked_by` is not empty
- task is blocked if unresolved `depends_on` exists
- blocked tasks cannot progress via `run-next`
- next task recommendation is deterministic and based on ready tasks sorting

Sorting rules:
- priority order: `urgent > high > medium > low > unknown`
- bugs before features when priority is equal
- then by task ID

### Backlog CLI
```bash
python run.py backlog
python run.py ready
python run.py blocked
python run.py next-task
python run.py add-dependency --id TASK-2 --depends-on TASK-1
python run.py remove-dependency --id TASK-2 --depends-on TASK-1
python run.py block --id TASK-1 --blocked-by "external: waiting for API contract" --reason "API contract is not finalized."
python run.py unblock --id TASK-1
```

## Decision Log / ADR Layer
Important architectural decisions are tracked as ADR-style records in:
- `decisions/index.json`
- `decisions/ADR-XXX.md`

Why decisions matter:
- preserve architectural intent over time
- avoid repeating rejected/superseded choices
- keep task execution aligned with accepted constraints

Linked decisions are task-scoped and included in agent input context for that task.
Agents must respect accepted decisions.

### Decision CLI
```bash
python run.py decisions
python run.py decision --id ADR-001
python run.py create-decision --title "..." --context "..." --decision "..." --consequences "..."
python run.py link-decision --id TASK-1 --decision ADR-001
python run.py unlink-decision --id TASK-1 --decision ADR-001
python run.py task-decisions --id TASK-1
```

## Release / Change Management Layer
Releases are stored in:
- `releases/releases.json`

Release statuses:
- `planned`
- `in_progress`
- `ready`
- `released`
- `cancelled`

This layer does not deploy anything. It only groups tasks/bugs into delivery packages and computes deterministic readiness.

Readiness highlights:
- release must contain at least one task
- all release tasks must be `done`
- no blocked tasks
- no QA verdict `failed`/`needs_rework`
- no approved-but-unapplied patch proposals
- missing command results and unknown QA are risks/warnings

### Release CLI
```bash
python run.py create-release --name "v0.1.0" --description "..."
python run.py releases
python run.py release --id REL-001
python run.py add-to-release --release REL-001 --task TASK-1
python run.py remove-from-release --release REL-001 --task TASK-1
python run.py release-status --id REL-001
python run.py release-readiness --id REL-001
python run.py release-notes --id REL-001
python run.py export-release-notes --id REL-001 --force
python run.py release-risks --id REL-001
python run.py rollback-plan --id REL-001
python run.py export-rollback-plan --id REL-001 --force
python run.py set-release-status --id REL-001 --status ready
```

## Supervisor Agent Layer
Supervisor converts natural language into explicit action proposals.

Safety model:
- dry-run by default (proposal only)
- `--execute` to apply supported actions
- `--yes` required for risky actions
- no arbitrary shell execution
- no bypassing orchestrator/business rules

Examples:
```bash
python run.py supervise --text "Create task to add healthcheck"
python run.py supervise --text "Create bug: login returns 500"
python run.py supervise --text "What should I do next?"
python run.py supervise --text "Run all tasks" --execute --yes
python run.py supervisor-actions
```

Telegram integration should call Supervisor later rather than low-level commands directly.

## Command Execution Layer
The system can run only allowlisted local validation commands and store results in task artifacts.

`command_results` stores:
- command
- exit_code
- success
- stdout/stderr
- started_at/finished_at
- duration_seconds
- source
- working_directory

### Allowed commands
- `python -m unittest discover -s tests`
- `python run.py validate`
- `python run.py agents`
- `python run.py config`
- `python run.py context`
- `python run.py list`

Arbitrary commands are rejected.
Implementation uses `subprocess.run(..., shell=False)` only.

### CLI
```bash
python run.py commands
python run.py run-command --id TASK-1 --command "python -m unittest discover -s tests"
python run.py run-plan-commands --id TASK-1
python run.py command-results --id TASK-1
```

Command results are available to QA as evidence.

## Repository Inspection Layer
The system supports safe read-only repository inspection and can attach structured repository context to a task.

Repository inspection is constrained:
- read-only only
- no absolute paths
- no `..`
- no `.env`
- no `.git/*`
- safe text files only (`.py`, `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.csv`)

### CLI
```bash
python run.py repo-scan
python run.py repo-tree
python run.py repo-file --path orchestrator.py
python run.py repo-search --query "orchestrator"
python run.py attach-repo-context --id TASK-1
python run.py repo-context --id TASK-1
```

When repository context is attached, Architect/Developer/QA receive this context in agent payloads and can reference existing paths more reliably.

## Patch Proposal & Safe Apply Layer
Developer may produce `patch_proposal`, but patches are not applied automatically.

```bash
python run.py patch --id TASK-1
python run.py export-patch --id TASK-1 --force
python run.py approve-patch --id TASK-1
python run.py apply-patch --id TASK-1 --force
```

Safety checks:
- no absolute paths
- no `..`
- no `.env`
- no `.git/*`
- no overwrite without `--force`

## QA Verification Gate
At `review`:
- `passed` -> `done`
- `failed` / `needs_rework` -> `ready_for_dev`
- `unknown` -> stays `review`

## Other Commands
```bash
python run.py create --title "..." --description "..."
python run.py create-bug --title "..." --description "..." --raw "..."
python run.py list
python run.py show --id TASK-1
python run.py decisions
python run.py decision --id ADR-001
python run.py create-decision --title "..." --context "..." --decision "..." --consequences "..."
python run.py link-decision --id TASK-1 --decision ADR-001
python run.py unlink-decision --id TASK-1 --decision ADR-001
python run.py task-decisions --id TASK-1
python run.py create-release --name "v0.1.0" --description "..."
python run.py releases
python run.py release --id REL-001
python run.py add-to-release --release REL-001 --task TASK-1
python run.py remove-from-release --release REL-001 --task TASK-1
python run.py release-status --id REL-001
python run.py release-readiness --id REL-001
python run.py release-notes --id REL-001
python run.py export-release-notes --id REL-001 --force
python run.py release-risks --id REL-001
python run.py rollback-plan --id REL-001
python run.py export-rollback-plan --id REL-001 --force
python run.py set-release-status --id REL-001 --status ready
python run.py backlog
python run.py ready
python run.py blocked
python run.py next-task
python run.py add-dependency --id TASK-2 --depends-on TASK-1
python run.py remove-dependency --id TASK-2 --depends-on TASK-1
python run.py block --id TASK-1 --blocked-by "..." --reason "..."
python run.py unblock --id TASK-1
python run.py run-next --id TASK-1
python run.py run-all
python run.py dev-plan --id TASK-1
python run.py export-dev-plan --id TASK-1 --force
python run.py qa-report --id TASK-1
python run.py export-qa-report --id TASK-1 --force
python run.py validate
python run.py agents
python run.py config
python run.py context
python run.py context --show
python -m unittest discover -s tests
```

## Current Limitations
- No Telegram integration yet.
- No database yet.
- No web server yet.
- No real image parsing yet.
- Unified diff is stored/exported but not deeply parsed for apply.
- Repository inspection is preview/search oriented and intentionally conservative.
- No deployment automation is implemented.
