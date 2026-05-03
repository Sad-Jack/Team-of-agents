# Архитектура

## Модули

- `run.py`: CLI-входная точка.
- `orchestrator.py`: структура задач, валидация, workflow-переходы.
- `agent_runner.py`: взаимодействие с агентными промптами.
- `llm_client.py`: провайдеры LLM (`fake`, `claude_code`, `openai`).
- `supervisor.py`: plan/execute для natural language команд.
- `backlog.py`: приоритезация, ready/blocked, зависимости.
- `release_manager.py`: релизы и readiness.
- `decision_log.py`: ADR-решения.
- `repo_inspector.py`: безопасный read-only доступ к репозиторию.
- `command_runner.py`: безопасный запуск allowlist-команд.
- `patch_utils.py`: patch proposal (approve/apply/export).
- `storage.py`: выбор backend-а хранения и операции над коллекциями (`tasks`, `releases`, `decisions_index`).

## Поток данных

1. Пользователь вызывает CLI.
2. `run.py` маршрутизирует в доменный модуль.
3. Изменения состояния проходят через `orchestrator.py`.
4. Артефакты сохраняются в JSON (`tasks/`, `releases/`, `decisions/`, `artifacts/`).

## Статусы workflow

- `idea`
- `refined`
- `ready_for_dev`
- `in_progress`
- `review`
- `done`

## Артефакты задачи

- анализ и критерии
- архитектурные заметки
- implementation plan
- patch proposal
- QA verification
- command_results
- repository_context

## Safety boundaries

- Без `shell=True`.
- Только allowlist-команды.
- Risky supervisor actions требуют подтверждения.
- Запрещены произвольные shell-команды.
- Инспекция репозитория только read-only и с path safety.

## Storage Abstraction Layer

Слой `storage.py` скрывает детали persistence backend:
- JSON (default)
- SQLite (optional)

Почему SQLite пока документ-ориентированный:
- минимальные изменения в текущей бизнес-логике
- сохраняется list-based API модулей
- миграция проще и безопаснее
- избегаем преждевременного сложного реляционного дизайна

## Managed Project Mode

- `managed_project.py` разделяет `system root` и `managed repo root`.
- `MANAGED_REPO_PATH` задаёт целевой проект относительно system root.
- `repo_inspector.py`, `patch_utils.py` и project-команды по умолчанию работают с managed repo root.
- System-команды `python3 run.py ...` выполняются в system root.
