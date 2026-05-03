# Commands

Examples below use `python` for brevity; on macOS/Linux you may use `python3`.
Internal allowlisted execution normalizes `python`/`python3` to the current interpreter (`sys.executable`).

- `python run.py create --title "..." --description "..."`
- `python run.py create-bug --title "..." --description "..." --raw "..."`
- `python run.py list`
- `python run.py backlog`
- `python run.py ready`
- `python run.py blocked`
- `python run.py next-task`
- `python run.py decisions`
- `python run.py decision --id ADR-001`
- `python run.py create-decision --title "..." --context "..." --decision "..." --consequences "..."`
- `python run.py link-decision --id TASK-1 --decision ADR-001`
- `python run.py unlink-decision --id TASK-1 --decision ADR-001`
- `python run.py task-decisions --id TASK-1`
- `python run.py create-release --name "v0.1.0" --description "..."`
- `python run.py releases`
- `python run.py release --id REL-001`
- `python run.py add-to-release --release REL-001 --task TASK-1`
- `python run.py remove-from-release --release REL-001 --task TASK-1`
- `python run.py release-status --id REL-001`
- `python run.py release-readiness --id REL-001`
- `python run.py release-notes --id REL-001`
- `python run.py export-release-notes --id REL-001 --force`
- `python run.py release-risks --id REL-001`
- `python run.py rollback-plan --id REL-001`
- `python run.py export-rollback-plan --id REL-001 --force`
- `python run.py set-release-status --id REL-001 --status ready`
- `python run.py supervisor-actions`
- `python run.py supervise --text "Create task to add healthcheck command"`
- `python run.py supervise --text "Run all tasks" --execute --yes`
- `python run.py add-dependency --id TASK-2 --depends-on TASK-1`
- `python run.py remove-dependency --id TASK-2 --depends-on TASK-1`
- `python run.py block --id TASK-1 --blocked-by "external: waiting" --reason "Waiting for dependency"`
- `python run.py unblock --id TASK-1`
- `python run.py show --id TASK-1`
- `python run.py run-next --id TASK-1`
- `python run.py run-all`
- `python run.py validate`
- `python run.py agents`
- `python run.py config`
- `python run.py context`
- `python run.py context --show`
- `python run.py repo-scan`
- `python run.py repo-tree`
- `python run.py repo-file --path orchestrator.py`
- `python run.py repo-search --query "orchestrator"`
- `python run.py attach-repo-context --id TASK-1`
- `python run.py repo-context --id TASK-1`
- `python -m unittest discover -s tests`
- `python3 -m unittest discover -s tests`

## Allowed Local Commands

Only these commands can be executed by the Command Execution Layer:
- `python -m unittest discover -s tests`
- `python3 -m unittest discover -s tests`
- `python run.py validate`
- `python3 run.py validate`
- `python run.py agents`
- `python3 run.py agents`
- `python run.py config`
- `python3 run.py config`
- `python run.py context`
- `python3 run.py context`
- `python run.py list`
- `python3 run.py list`

Arbitrary shell commands are rejected.
