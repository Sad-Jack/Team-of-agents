# Managed Project Mode

## Назначение

`Team of Agents` может жить внутри другого репозитория и управлять именно внешним проектом, а не только своими внутренними файлами.

## Два корня

- `system root` — директория Team of Agents
- `managed repo root` — целевой проект

`MANAGED_REPO_PATH` задаёт managed root относительно system root.

## Примеры структуры

```text
my-project/
  src/
  tests/
  README.md
  team-agents/
    run.py
    orchestrator.py
    ...
```

Пример `.env` в `team-agents`:

```env
MANAGED_REPO_PATH=..
```

## Команды

```bash
python3 run.py managed-project
python3 run.py managed-project-check
python3 run.py doctor
python3 run.py repo-scan
```

## Что работает в managed root по умолчанию

- инспекция репозитория (`repo-scan`, `repo-tree`, `repo-file`, `repo-search`)
- `attach-repo-context`
- применение patch proposal
- project-команды выполнения (например, `python3 -m unittest discover -s tests`)

System-команды `python3 run.py ...` остаются в `system root`.

## Troubleshooting

- Неверный путь: проверьте `MANAGED_REPO_PATH` и `python3 run.py managed-project-check`.
- Сканируется `team-agents` вместо целевого проекта: установите `MANAGED_REPO_PATH=..`.
- Патч применяется не туда: проверьте `managed_repo_root` в `python3 run.py managed-project`.
- Команда запускается не в той директории: для system-команд используйте `python3 run.py ...`, для project-команд — обычные allowlist-команды проекта.
