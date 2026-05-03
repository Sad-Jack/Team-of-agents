# Руководство по использованию

## 1. Базовая проверка окружения

```bash
python3 -m unittest discover -s tests
python3 run.py validate
python3 run.py config
```

## 2. Сценарий: идея -> done

1. Создайте задачу:
```bash
python3 run.py create --title "Добавить healthcheck" --description "CLI команда healthcheck"
```
2. Посмотрите список:
```bash
python3 run.py list
```
3. Прогоните шаги workflow:
```bash
python3 run.py run-next --id TASK-1
python3 run.py run-next --id TASK-1
python3 run.py run-next --id TASK-1
python3 run.py run-next --id TASK-1
```
4. Проверьте статус:
```bash
python3 run.py show --id TASK-1
```

## 3. Сценарий: баг -> QA

1. Создайте баг:
```bash
python3 run.py create-bug --title "Login 500" --description "Падает логин" --raw "Traceback..."
```
2. Прогоните баг по шагам:
```bash
python3 run.py run-next --id BUG-1
```
3. Посмотрите QA-отчет:
```bash
python3 run.py qa-report --id BUG-1
```

## 4. Сценарий: developer plan -> patch -> command results -> QA

1. Получите план:
```bash
python3 run.py dev-plan --id TASK-1
```
2. Посмотрите patch proposal:
```bash
python3 run.py patch --id TASK-1
```
3. Одобрите и примените patch (если нужно):
```bash
python3 run.py approve-patch --id TASK-1
python3 run.py apply-patch --id TASK-1 --force
```
4. Запустите проверочную команду:
```bash
python3 run.py run-command --id TASK-1 --command "python3 run.py validate"
```
5. Проверьте command results:
```bash
python3 run.py command-results --id TASK-1
```

## 5. Сценарий: release -> release notes

1. Создайте релиз:
```bash
python3 run.py create-release --name "v0.1.0" --description "Первый релиз"
```
2. Добавьте задачу:
```bash
python3 run.py add-to-release --release REL-001 --task TASK-1
```
3. Проверьте readiness:
```bash
python3 run.py release-readiness --id REL-001
```
4. Сгенерируйте notes:
```bash
python3 run.py release-notes --id REL-001
```

## 6. Сценарий: Telegram интерфейс

1. Настройте `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`, `TELEGRAM_DRY_RUN_BY_DEFAULT`.
2. Проверьте конфиг:
```bash
python3 run.py telegram-config
```
3. Запустите polling-бот:
```bash
python3 run.py telegram
```
4. Примеры в чате:
- `/dryrun Создай задачу на healthcheck`
- `/execute Покажи backlog`
- `/yes run command python3 run.py validate`

## Установка внутри другого проекта

Если `team-agents` находится внутри целевого репозитория:

```text
my-project/
  src/
  tests/
  team-agents/
```

в `.env` внутри `team-agents` укажите:

```env
MANAGED_REPO_PATH=..
```

Проверка:

```bash
python3 run.py managed-project
python3 run.py managed-project-check
python3 run.py repo-scan
```

## PM workflow (high-level)

Пример работы как с единым менеджером:
1. Создать задачу или баг.
2. Выполнить `prepare-task` до `ready_for_dev`.
3. Проверить `project-status` и `next-work`.
4. Добавить заметки по обсуждению через `add-note`.
5. Контролировать блокеры через `blockers`.
