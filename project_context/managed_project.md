# Managed Project Mode

Система разделяет два корня:
- `system root`: директория, где лежит Team of Agents
- `managed repo root`: целевой проект, которым управляет система

Переменная `MANAGED_REPO_PATH` задаёт путь к целевому проекту относительно `system root`.

Типовые варианты:
- `MANAGED_REPO_PATH=.` — управляем текущий репозиторий
- `MANAGED_REPO_PATH=..` — Team of Agents встроен в поддиректорию чужого проекта

В Managed Project Mode по умолчанию на целевой проект направлены:
- `repo-scan`, `repo-tree`, `repo-file`, `repo-search`
- `attach-repo-context`
- применение patch proposal
- project-команды выполнения (например, тесты)

System-команды Team of Agents (например, `python3 run.py ...`) остаются в system root.

Важно:
- Не модифицировать внутренности Team of Agents, если это не было явно запрошено.
- Не путать system root и managed repo root в автоматизациях и патчах.
