# Architecture

- `orchestrator.py` owns workflow state and status transitions.
- `agent_runner.py` owns agent prompt execution and JSON parsing.
- `llm_client.py` owns provider selection and client calls.
- `agents/*.md` define role behavior.
- `tasks/tasks.json` is the source of truth for persisted tasks.
- `project_context/*.md` provides reusable project knowledge for all agents.
- `backlog.py` owns dependency, blocked/ready, and next-task recommendation logic.
- `decision_log.py` owns ADR creation, indexing, and task linking.
- `decision_context_loader.py` loads only task-related decisions for agent context.
- `supervisor.py` is a safe command router that plans actions from natural language.
- Blocked tasks must not progress through workflow steps.
