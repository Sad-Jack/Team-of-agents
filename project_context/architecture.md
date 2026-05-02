# Architecture

- `orchestrator.py` owns workflow state and status transitions.
- `agent_runner.py` owns agent prompt execution and JSON parsing.
- `llm_client.py` owns provider selection and client calls.
- `agents/*.md` define role behavior.
- `tasks/tasks.json` is the source of truth for persisted tasks.
- `project_context/*.md` provides reusable project knowledge for all agents.
