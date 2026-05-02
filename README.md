# Team of Agents

## Что это

Локальная система управления разработкой с AI-агентами.
Она помогает вести идеи, задачи и баги, поддерживает backlog, релизы, ADR-решения, QA-проверки и управляемый workflow между агентами.

## Зачем это нужно

- превращать идеи в структурированные задачи
- заводить баги из сырого текста и логов
- прогонять задачи через `Analyst -> Architect -> Developer -> QA`
- хранить архитектурные решения (ADR)
- управлять backlog, зависимостями и блокерами
- собирать релизные пакеты и проверять readiness
- готовить developer plan, QA report, release notes
- в будущем подключить Telegram-слой

## Как устроена система

- `run.py` — основной CLI
- `orchestrator.py` — управление состояниями задач и переходами workflow
- `agent_runner.py` — запуск агентных промптов и парсинг JSON-ответов
- `llm_client.py` — выбор и вызов LLM-провайдера (`fake`, `claude_code`, `openai`)
- `supervisor.py` — планирование/безопасное выполнение действий из natural language
- `backlog.py` — логика ready/blocked и сортировка backlog
- `release_manager.py` — релизы, readiness, release notes/risks/rollback
- `decision_log.py` — ADR-слой (`decisions/index.json` + `decisions/ADR-*.md`)
- `repo_inspector.py` — безопасная read-only инспекция репозитория
- `command_runner.py` — allowlist-выполнение локальных команд
- `patch_utils.py` — экспорт/апрув/применение patch proposal
- `project_context/` — постоянный контекст проекта
- `agents/` — промпты ролей
- `tasks/`, `releases/`, `decisions/`, `artifacts/` — данные и артефакты

## Быстрый старт

```bash
python3 -m unittest discover -s tests
python3 run.py validate
python3 run.py create --title "Добавить healthcheck" --description "CLI healthcheck"
python3 run.py list
python3 run.py run-next --id TASK-1
```

## Подключение Claude Code

1. Установите Claude Code отдельно.
2. Авторизуйтесь в Claude Code через вашу Claude-подписку.
3. В `.env` установите `LLM_PROVIDER=claude_code`.
4. Если хотите избегать API-биллинга, не задавайте `ANTHROPIC_API_KEY`.
5. Проверьте конфиг и smoke:

```bash
python3 run.py config
python3 run.py llm-smoke --prompt "Return JSON: {\"ok\": true}"
```

## Storage Backends

Система поддерживает два backend-а хранения:
- `json` (по умолчанию)
- `sqlite` (опционально)

Переменные окружения:

```env
STORAGE_BACKEND=json
SQLITE_DB_PATH=data/team_agents.db
```

Команды:

```bash
python3 run.py storage-info
python3 run.py storage-init
python3 run.py migrate-json-to-sqlite --force
python3 run.py export-sqlite-to-json --force
```

Когда использовать SQLite:
- если нужен один файл БД вместо нескольких JSON
- если нужна более предсказуемая атомарность записи

Рекомендация:
- перед миграцией сделайте backup JSON-файлов.

## Managed Project Mode

Система поддерживает embedded-сценарий:

```text
my-project/
  src/
  tests/
  team-agents/
```

Где:
- `system root` — `team-agents/`
- `managed repo root` — целевой проект (например, `my-project/`)

Настройка:

```env
MANAGED_REPO_PATH=..
```

Проверка:

```bash
python3 run.py managed-project
python3 run.py managed-project-check
```

В этом режиме по умолчанию на managed repo направлены:
- repo inspection
- attach-repo-context
- применение patch proposal
- project-команды выполнения

System-команды `python3 run.py ...` выполняются в system root.

## Telegram Bot Interface

MVP-бот работает в polling-режиме и выступает только интерфейсным слоем к `Supervisor`.

Как настроить:
1. Создайте бота через `@BotFather` и получите `TELEGRAM_BOT_TOKEN`.
2. Получите свой `Telegram user id` (например, через `@userinfobot`) и запишите в `TELEGRAM_OWNER_ID`.
3. Заполните `.env`:
   - `TELEGRAM_BOT_TOKEN=...`
   - `TELEGRAM_OWNER_ID=...`
   - `TELEGRAM_DRY_RUN_BY_DEFAULT=true`
4. Проверьте конфиг и запустите:

```bash
python3 run.py telegram-config
python3 run.py telegram
```

Безопасность:
- owner-only: доступ только для `TELEGRAM_OWNER_ID`
- по умолчанию dry-run (настраивается флагом `TELEGRAM_DRY_RUN_BY_DEFAULT`)
- рискованные действия требуют `/yes ...`
- токен не печатается в конфиге и логах

## Голосовые сообщения в Telegram

Пайплайн голосового ввода:
- Telegram voice -> `ffmpeg` (конвертация в WAV) -> STT-провайдер -> `Supervisor`
- `ffmpeg` только конвертирует аудио, распознавание делает STT-слой
- по умолчанию голос выключен: `STT_PROVIDER=disabled`

Как включить `whisper_cli`:

```env
STT_PROVIDER=whisper_cli
FFMPEG_BINARY=ffmpeg
VOICE_WORK_DIR=.tmp/voice
WHISPER_CLI_BINARY=whisper
WHISPER_MODEL=small
WHISPER_LANGUAGE=ru
VOICE_KEEP_FILES=false
```

Проверка:

```bash
python3 run.py voice-config
python3 run.py transcribe-file --path sample.ogg
```

Замечания по безопасности:
- временные файлы хранятся в `.tmp/voice`
- по умолчанию удаляются после обработки (`VOICE_KEEP_FILES=false`)
- рискованные действия по распознанному тексту по-прежнему требуют `/yes ...`

## Проверка готовности MVP

```bash
python3 run.py doctor
python3 run.py demo-reset --yes
python3 run.py demo-seed
python3 run.py e2e-demo
```

Рекомендация:
Перед добавлением новых архитектурных слоев запускайте `doctor` + тесты + `e2e-demo`.

## Основные сценарии

1. Создать задачу: `python3 run.py create ...`
2. Создать баг: `python3 run.py create-bug ...`
3. Прогнать задачу по workflow: `python3 run.py run-next --id TASK-1`
4. Посмотреть backlog: `python3 run.py backlog`
5. Прикрепить контекст репозитория: `python3 run.py attach-repo-context --id TASK-1`
6. Получить developer plan: `python3 run.py dev-plan --id TASK-1`
7. Посмотреть QA report: `python3 run.py qa-report --id TASK-1`
8. Создать ADR: `python3 run.py create-decision ...`
9. Создать release: `python3 run.py create-release --name "v0.1.0"`
10. Использовать supervisor: `python3 run.py supervise --text "..."`
11. Использовать Telegram: `/dryrun ...`, `/execute ...`, `/yes ...`

## Основные команды

### Задачи
- `python3 run.py create --title "..." --description "..."`
- `python3 run.py list`
- `python3 run.py show --id TASK-1`

### Баги
- `python3 run.py create-bug --title "..." --description "..." --raw "..."`

### Workflow
- `python3 run.py run-next --id TASK-1`
- `python3 run.py run-all`
- `python3 run.py validate`

### Backlog
- `python3 run.py backlog`
- `python3 run.py ready`
- `python3 run.py blocked`
- `python3 run.py next-task`
- `python3 run.py add-dependency --id TASK-2 --depends-on TASK-1`
- `python3 run.py remove-dependency --id TASK-2 --depends-on TASK-1`
- `python3 run.py block --id TASK-1 --blocked-by "external: wait" --reason "..."`
- `python3 run.py unblock --id TASK-1`

### Repo Context
- `python3 run.py repo-scan`
- `python3 run.py repo-tree`
- `python3 run.py repo-search --query "orchestrator"`
- `python3 run.py repo-file --path run.py`
- `python3 run.py attach-repo-context --id TASK-1`
- `python3 run.py repo-context --id TASK-1`

### Developer / Patch
- `python3 run.py dev-plan --id TASK-1`
- `python3 run.py export-dev-plan --id TASK-1 --force`
- `python3 run.py patch --id TASK-1`
- `python3 run.py approve-patch --id TASK-1`
- `python3 run.py apply-patch --id TASK-1 --force`

### Commands / Tests
- `python3 run.py commands`
- `python3 run.py run-command --id TASK-1 --command "python3 run.py validate"`
- `python3 run.py run-plan-commands --id TASK-1`
- `python3 run.py command-results --id TASK-1`
- `python3 -m unittest discover -s tests`

### QA
- `python3 run.py qa-report --id TASK-1`
- `python3 run.py export-qa-report --id TASK-1 --force`

### Decisions
- `python3 run.py decisions`
- `python3 run.py decision --id ADR-001`
- `python3 run.py create-decision --title "..." --context "..." --decision "..." --consequences "..."`
- `python3 run.py link-decision --id TASK-1 --decision ADR-001`
- `python3 run.py unlink-decision --id TASK-1 --decision ADR-001`
- `python3 run.py task-decisions --id TASK-1`

### Releases
- `python3 run.py create-release --name "v0.1.0" --description "..."`
- `python3 run.py releases`
- `python3 run.py release --id REL-001`
- `python3 run.py add-to-release --release REL-001 --task TASK-1`
- `python3 run.py remove-from-release --release REL-001 --task TASK-1`
- `python3 run.py release-readiness --id REL-001`
- `python3 run.py release-notes --id REL-001`
- `python3 run.py release-risks --id REL-001`
- `python3 run.py rollback-plan --id REL-001`
- `python3 run.py set-release-status --id REL-001 --status ready`

### Supervisor
- `python3 run.py supervisor-actions`
- `python3 run.py supervise --text "Что делать дальше?"`
- `python3 run.py supervise --text "run command python3 run.py validate" --execute`
- `python3 run.py supervise --text "run command python3 run.py validate" --execute --yes`

### Config
- `python3 run.py config`
- `python3 run.py llm-smoke --prompt "Return JSON: {\"ok\": true}"`

### Telegram
- `python3 run.py telegram-config`
- `python3 run.py telegram`
- `/start`
- `/help`
- `/status`
- `/actions`
- `/dryrun ...`
- `/execute ...`
- `/yes ...`

### MVP demo
- `python3 run.py doctor`
- `python3 run.py demo-reset --yes`
- `python3 run.py demo-seed`
- `python3 run.py demo`
- `python3 run.py e2e-demo`

### Storage
- `python3 run.py storage-info`
- `python3 run.py storage-init`
- `python3 run.py migrate-json-to-sqlite --force`
- `python3 run.py export-sqlite-to-json --force`

## Архитектурные правила

- LLM-ответ не меняет статусы напрямую
- переходами статусов владеет только `orchestrator.py`
- risky-действия требуют явного подтверждения
- Command Execution Layer работает только по allowlist
- все subprocess-вызовы без `shell=True`
- секреты не хранятся в репозитории
- в тестах используется `fake` провайдер

## Текущие ограничения

- webhook режим не используется (только polling для MVP)
- хранение данных в JSON
- базы данных нет
- прямой анализ скриншотов не реализован
- применение патчей явно контролируется
- работа через Claude Code зависит от лимитов вашей подписки

## Roadmap

- Telegram webhook mode
- SQLite/PostgreSQL
- multi-project support
- более удобный UI
- более глубокие workflow для Claude Code

## Единый Project Manager

Система поддерживает режим единого менеджера проекта: пользователь общается на высоком уровне (CLI/Telegram/voice), а внутренняя маршрутизация идёт через `Supervisor` и `project_manager.py`.

Примеры запросов:
- "Создай задачу ..."
- "Подготовь TASK-1 к разработке"
- "Что делать дальше?"
- "Добавь заметку к TASK-1: ..."
- "Дай статус проекта"

Важно:
- менеджер не обходит safety-гейты;
- статус не меняется напрямую из LLM-ответа;
- патчи не применяются автоматически.

## Контекст диалога и активный фокус

Можно установить фокус на задаче/релизе/решении и использовать follow-up команды без повторения ID.

Примеры:
- `Обсудим TASK-1`
- `Добавь заметку: проверить edge case`
- `Что по ней?`
- `Подготовь её к разработке`

CLI:
- `python3 run.py focus`
- `python3 run.py focus-task --id TASK-1`
- `python3 run.py focus-release --id REL-001`
- `python3 run.py focus-decision --id ADR-001`
- `python3 run.py clear-focus`
