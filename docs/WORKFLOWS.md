# Рабочие сценарии

## Сценарий 1: idea -> ready task

1. `python3 run.py create --title "..." --description "..."`
2. `python3 run.py run-next --id TASK-1` (analyst)
3. `python3 run.py run-next --id TASK-1` (architect)
4. Получаем `ready_for_dev`.

## Сценарий 2: bug intake

1. `python3 run.py create-bug --title "..." --description "..." --raw "..."`
2. `bug_intake` структурирует отчет в `artifacts.bug_report`.

## Сценарий 3: developer plan -> patch -> command results -> QA

1. `python3 run.py run-next --id TASK-1` (developer)
2. `python3 run.py dev-plan --id TASK-1`
3. `python3 run.py patch --id TASK-1`
4. `python3 run.py approve-patch --id TASK-1`
5. `python3 run.py apply-patch --id TASK-1 --force`
6. `python3 run.py run-command --id TASK-1 --command "python3 run.py validate"`
7. `python3 run.py run-next --id TASK-1` (qa)

## Сценарий 4: backlog / dependencies

1. Добавьте зависимость: `add-dependency`
2. При необходимости блокируйте: `block`
3. Проверяйте `ready` / `blocked` / `next-task`.

## Сценарий 5: decision log (ADR)

1. `python3 run.py create-decision ...`
2. `python3 run.py link-decision --id TASK-1 --decision ADR-001`
3. `python3 run.py task-decisions --id TASK-1`

## Сценарий 6: release management

1. `python3 run.py create-release --name "v0.1.0"`
2. `python3 run.py add-to-release --release REL-001 --task TASK-1`
3. `python3 run.py release-readiness --id REL-001`
4. `python3 run.py release-notes --id REL-001`
5. `python3 run.py rollback-plan --id REL-001`

## Сценарий 7: supervisor natural language control

1. План: `python3 run.py supervise --text "Что делать дальше?"`
2. Выполнение безопасных действий: `--execute`
3. Для risky действий добавьте `--yes`.

## High-level: подготовка задачи

- `prepare-task --id TASK-1` доводит задачу до `ready_for_dev`.
- Если задача заблокирована, процесс останавливается с объяснением.

## High-level: статус проекта

- `project-status` даёт сводку по задачам, релизам, ready/blocked.
- `next-work` показывает следующую рекомендуемую задачу.

## High-level: обсуждение задачи

- `add-note --id TASK-1 --text "..."`
- `notes --id TASK-1`
- `task-discussion --id TASK-1`
