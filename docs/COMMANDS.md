# Команды CLI

## Задачи

- `python3 run.py create --title "..." --description "..."`
- `python3 run.py list`
- `python3 run.py show --id TASK-1`

## Баги

- `python3 run.py create-bug --title "..." --description "..." --raw "..."`

## Workflow

- `python3 run.py run-next --id TASK-1`
- `python3 run.py run-all`
- `python3 run.py validate`

## Backlog

- `python3 run.py backlog`
- `python3 run.py ready`
- `python3 run.py blocked`
- `python3 run.py next-task`
- `python3 run.py add-dependency --id TASK-2 --depends-on TASK-1`
- `python3 run.py remove-dependency --id TASK-2 --depends-on TASK-1`
- `python3 run.py block --id TASK-1 --blocked-by "external: wait" --reason "..."`
- `python3 run.py unblock --id TASK-1`

## Repo context

- `python3 run.py repo-scan`
- `python3 run.py repo-tree`
- `python3 run.py repo-search --query "orchestrator"`
- `python3 run.py repo-file --path run.py`
- `python3 run.py attach-repo-context --id TASK-1`
- `python3 run.py repo-context --id TASK-1`

## Developer / Patch

- `python3 run.py dev-plan --id TASK-1`
- `python3 run.py export-dev-plan --id TASK-1 --force`
- `python3 run.py patch --id TASK-1`
- `python3 run.py export-patch --id TASK-1 --force`
- `python3 run.py approve-patch --id TASK-1`
- `python3 run.py apply-patch --id TASK-1 --force`

## Commands / Tests

- `python3 run.py commands`
- `python3 run.py run-command --id TASK-1 --command "python3 run.py validate"`
- `python3 run.py run-plan-commands --id TASK-1`
- `python3 run.py command-results --id TASK-1`
- `python3 -m unittest discover -s tests`

## QA

- `python3 run.py qa-report --id TASK-1`
- `python3 run.py export-qa-report --id TASK-1 --force`

## Decisions

- `python3 run.py decisions`
- `python3 run.py decision --id ADR-001`
- `python3 run.py create-decision --title "..." --context "..." --decision "..." --consequences "..."`
- `python3 run.py link-decision --id TASK-1 --decision ADR-001`
- `python3 run.py unlink-decision --id TASK-1 --decision ADR-001`
- `python3 run.py task-decisions --id TASK-1`

## Releases

- `python3 run.py create-release --name "v0.1.0" --description "..."`
- `python3 run.py releases`
- `python3 run.py release --id REL-001`
- `python3 run.py add-to-release --release REL-001 --task TASK-1`
- `python3 run.py remove-from-release --release REL-001 --task TASK-1`
- `python3 run.py release-status --id REL-001`
- `python3 run.py release-readiness --id REL-001`
- `python3 run.py release-notes --id REL-001`
- `python3 run.py release-risks --id REL-001`
- `python3 run.py rollback-plan --id REL-001`
- `python3 run.py set-release-status --id REL-001 --status ready`

## Supervisor

- `python3 run.py supervisor-actions`
- `python3 run.py supervise --text "Create task ..."`
- `python3 run.py supervise --text "run command python3 run.py validate" --execute`
- `python3 run.py supervise --text "run command python3 run.py validate" --execute --yes`

## Config

- `python3 run.py config`
- `python3 run.py llm-smoke --prompt "Return JSON: {\"ok\": true}"`

## Telegram

- `python3 run.py telegram-config`
- `python3 run.py telegram`

## Voice / STT

- `python3 run.py voice-config`
- `python3 run.py transcribe-file --path sample.ogg`

## MVP readiness/demo

- `python3 run.py doctor`
- `python3 run.py demo-reset --yes`
- `python3 run.py demo-seed`
- `python3 run.py demo`
- `python3 run.py e2e-demo`

## Storage

- `python3 run.py storage-info`
- `python3 run.py storage-init`
- `python3 run.py migrate-json-to-sqlite`
- `python3 run.py migrate-json-to-sqlite --force`
- `python3 run.py export-sqlite-to-json`
- `python3 run.py export-sqlite-to-json --force`

## Managed project

- `python3 run.py managed-project`
- `python3 run.py managed-project-check`

## Project Manager

- `python3 run.py project-status`
- `python3 run.py task-status --id TASK-1`
- `python3 run.py prepare-task --id TASK-1`
- `python3 run.py advance-task --id TASK-1`
- `python3 run.py advance-task --id TASK-1 --target ready_for_dev`
- `python3 run.py next-work`
- `python3 run.py blockers`
- `python3 run.py add-note --id TASK-1 --text "..."`
- `python3 run.py notes --id TASK-1`
- `python3 run.py task-discussion --id TASK-1`
- `python3 run.py release-summary --id REL-001`

## Conversation context / focus

- `python3 run.py focus`
- `python3 run.py focus-task --id TASK-1`
- `python3 run.py focus-release --id REL-001`
- `python3 run.py focus-decision --id ADR-001`
- `python3 run.py clear-focus`
- `python3 run.py sessions`
- `python3 run.py session --id cli:default`
- `python3 run.py supervise --text "Добавь заметку: ... " --execute --session-id cli:default`
