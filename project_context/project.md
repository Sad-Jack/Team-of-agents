# Project

Name: Multi-Agent Project Management MVP

Purpose:
- Manage feature and bug work through a strict agent workflow.
- Keep state deterministic, testable, and file-based.

Current MVP Scope:
- Feature task flow and bug intake flow.
- LLM adapter with fake provider default and optional OpenAI provider.
- CLI operations for create/list/show/run/validate/config/context.
- Backlog and dependency management with blocked/ready state.
- Release/change management with deterministic readiness checks.
- Supervisor layer for safe natural-language action routing.

What the system does:
- Stores tasks in JSON.
- Runs agent steps with structured artifacts.
- Preserves workflow ownership in orchestrator.
- Recommends next task from deterministic ready-task ordering.
- Groups tasks into releases and generates notes/risks/rollback plans without deployment.
- Provides Supervisor planning (proposal first, explicit execution second).

Out of scope:
- Telegram integration.
- Database persistence.
- Web server.
- Direct image recognition.
