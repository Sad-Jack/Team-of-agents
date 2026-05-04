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

## Быстрый запуск

### 1. Первичная настройка

```bash
./setup.sh
```

Создаёт `.venv`, устанавливает зависимости, копирует `.env.example` → `.env`.

### 2. Заполнить `.env`

```env
TELEGRAM_BOT_TOKEN=<токен от @BotFather>
TELEGRAM_OWNER_ID=<твой Telegram user id>
```

### 3. Проверить

```bash
python3 run.py telegram-config
python3 run.py doctor
```

`.env` подхватывается автоматически — вручную делать `source .env` не нужно.

### 4. Запустить Telegram-бота

```bash
./start.sh
```

Или через make:

```bash
make setup
make start
```

---

## Быстрый старт (CLI без Telegram)

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

## Telegram UX Layer

### Подтверждение действий кнопками

При dry-run (по умолчанию) бот показывает план и inline-кнопки:

```
Plan: create_task ...
[✅ Выполнить]  [❌ Отмена]
```

- **✅ Выполнить** — выполняет safe-действие немедленно
- **❌ Отмена** — отменяет pending action
- Рискованные действия (`RISKY_ACTIONS`) через кнопку не выполняются — требуют `/yes <запрос>`

### Status chat для событий агентов (task log chat)

Добавь в `.env`:
```env
TELEGRAM_STATUS_CHAT_ID=<id чата/канала для уведомлений>
TELEGRAM_NOTIFY_AGENT_EVENTS=true
```

Когда задача или баг создаётся через Telegram, бот отправляет карточку в status chat:

```
🧩 TASK-12: Add healthcheck command
Статус: idea
Приоритет: medium
Описание: Add a healthcheck endpoint to verify system health.
—
Ответь на это сообщение, чтобы работать с задачей.
```

**Reply-based task control:** ответив на карточку задачи/бага в status chat, можно управлять ею на естественном языке:
- `бери в работу`
- `доработай описание`
- `добавь, что нужно проверить на Mac`
- `что по этой задаче?`

Бот находит связанную задачу локально (без LLM-вызова), добавляет контекст и передаёт в Supervisor.

Как получить TELEGRAM_STATUS_CHAT_ID: добавь бота в нужный чат/канал и отправь сообщение, затем используй `@userinfobot` или Telegram API `/getUpdates`.

### /start как entrypoint Project Manager-а

`/start` показывает режим работы:
- **self-managed** — `MANAGED_REPO_PATH=.` (бот управляет самим Team-of-agents)
- **embedded** — `MANAGED_REPO_PATH=..` (бот управляет внешним проектом)

Кнопка **🔍 Изучить проект** запускает безопасный repo scan:
- подтверждает managed repo root
- считает файлы в проекте
- показывает предупреждения и подсказывает следующие шаги

## Telegram Board / Admin Room

Бот может публиковать события проекта в отдельную Telegram forum-группу.
Каждая область проекта имеет свой топик (форум-тред).

### Топики Board

| Топик | Содержимое |
|---|---|
| 💡 Task Ideas | Новые идеи задач |
| ✅ Task Ready | Задачи готовые к разработке |
| 🚧 Task Active | Задачи в работе |
| ⛔ Task Blocked | Заблокированные задачи |
| 🐞 Bugs New | Новые баги |
| 🛠 Bugs Active | Баги в работе |
| 🟡 Needs Input | Требуют уточнения |
| 🚀 Releases | Релизы |
| 🧾 Agent Log | Лог событий агентов |
| 📌 Decisions | ADR-решения |

### Настройка

1. Создай forum-группу в Telegram (**New Group → Enable Topics**).
2. Создай топики из таблицы выше.
3. Добавь бота администратором с правом **Post Messages**.
4. Заполни в `.env` (не коммить `.env`!):

```env
TELEGRAM_BOARD_ENABLED=true
TELEGRAM_BOARD_CHAT_ID=-1003952202151   # пример

TELEGRAM_TOPIC_TASK_IDEAS=10            # пример
TELEGRAM_TOPIC_TASK_READY=11
# ... остальные топики
```

### Как получить значения из ссылки на топик

Ссылка на топик: `t.me/c/3952202151/10`

Означает:
```
TELEGRAM_BOARD_CHAT_ID=-1003952202151   # добавь -100 перед id
TELEGRAM_TOPIC_RELEASES=10              # число после /
```

### Проверить конфигурацию

```bash
python3 run.py board-config        # конфигурация Board (topic id, missing vars)
python3 run.py telegram-config     # общий Telegram-конфиг (только _SET флаги)
```

В Telegram (приватный чат с ботом):
```
/board_config
```

### Smoke-test топиков

Проверить, что бот реально может писать в каждый топик:

```bash
# Показать что будет отправлено, без вызова API:
python3 run.py board-ping --dry-run

# Только один топик (dry-run):
python3 run.py board-ping --topic agent_log --dry-run

# Реальный smoke-test (требует TELEGRAM_BOT_TOKEN + Board vars):
python3 run.py board-ping

# Только один топик:
python3 run.py board-ping --topic agent_log
```

В Telegram: `/board_ping`

Результат:
```
Telegram Board ping result:
✅ Task Ideas
✅ Task Ready
⚠️ Agent Log — timeout: сообщение могло быть отправлено, проверь топик
❌ Task Active — Forbidden: bot is not a member
— Needs Input (not configured)
```

Доступные ключи для `--topic`: `task_ideas`, `task_ready`, `task_active`, `task_blocked`,
`bugs_new`, `bugs_active`, `needs_input`, `releases`, `agent_log`, `decisions`.

Таймаут управляется через `TELEGRAM_BOARD_SEND_TIMEOUT_SECONDS` (по умолчанию 20 с).
`⚠️ timeout` — не означает ошибку: сообщение могло дойти, нужно проверить топик.

### Публикация карточки задачи (board-post-task)

Команда публикует карточку конкретной задачи в нужный топик Board и обновляет её при повторном вызове:

```bash
# Превью без отправки:
python3 run.py board-post-task TASK-1 --dry-run

# Создать или обновить карточку:
python3 run.py board-post-task TASK-1

# Всегда создавать новую карточку (игнорировать существующую):
python3 run.py board-post-task TASK-1 --force-new
```

**Поведение (upsert):**

| Ситуация | Действие |
|---|---|
| Карточки нет → | создаёт новое сообщение, сохраняет mapping |
| Карточка есть, топик тот же → | редактирует существующее сообщение |
| Карточка есть, топик изменился → | создаёт карточку в новом топике, архивирует старую |
| `--force-new` → | всегда создаёт новое сообщение |

**Перенос между топиками (topic move):**

Когда статус задачи меняется (например `idea` → `in_progress`), топик тоже меняется.
Telegram не умеет перемещать сообщения между топиками, поэтому:
1. Новая карточка отправляется в правильный топик.
2. Старая карточка заменяется коротким «надгробным» сообщением:

```
➡️ TASK-1 перенесена

Актуальная карточка теперь находится в другом топике.
Новый статус: В работе
```

Архивирование старой карточки управляется переменной:

```env
TELEGRAM_BOARD_ARCHIVE_OLD_ON_MOVE=true   # по умолчанию: архивировать
# false — не редактировать старое сообщение, просто создать новое
```

Если редактирование старой карточки не удалось — новая карточка уже создана и mapping обновлён; операция считается успешной со статусом `moved_archive_failed`.

### Авто-синхронизация Board

После любых изменений задачи через CLI или бот карточка автоматически обновляется на Board:

- `create`, `create-bug` — создаёт новую карточку
- `run-next`, `advance-task`, `prepare-task` — обновляет существующую карточку
- `run-all` — обновляет все затронутые карточки

Авто-синхронизация включена по умолчанию при `TELEGRAM_BOARD_ENABLED=true`. Чтобы отключить:

```env
TELEGRAM_BOARD_AUTO_SYNC=false
```

Вручную обновить карточку одной задачи:

```bash
python3 run.py board-post-task TASK-1
```

### Важные правила Board

- Board — только для отображения. Все рабочие команды — через приватный чат.
- Реальные значения заполняются вручную в локальном `.env`.
- **Не коммить `.env`** с реальными значениями.
- Источник истины — локальное хранилище, не Telegram.

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

### Voice input через mlx-whisper (Apple Silicon, бесплатно, без API)

Если `whisper` CLI недоступен, но есть Apple Silicon — используй `mlx-whisper` через `custom_cli`:

**Установка:**

```bash
pip install mlx-whisper
python -c "import mlx_whisper; print('mlx_whisper ok')"
```

**Настройка `.env`:**

```env
STT_PROVIDER=custom_cli
STT_CUSTOM_COMMAND=python scripts/stt_mlx_whisper.py --audio-path {audio_path} --model mlx-community/whisper-small-mlx --language ru
```

> `{audio_path}` — плейсхолдер, подставляется автоматически. Менять не нужно.

**Проверка:**

```bash
python3 run.py voice-config
```

**Запуск бота:**

```bash
python3 run.py telegram
```

**Важно:**
- Первый запуск скачивает модель в кеш (~150 MB для `whisper-small`). Последующие — мгновенны.
- Если venv проекта использует Python 3.14+ и `mlx-whisper` требует 3.11/3.12 — создай отдельный venv и укажи путь к его `python`:
  ```env
  STT_CUSTOM_COMMAND=/path/to/stt-venv/bin/python scripts/stt_mlx_whisper.py --audio-path {audio_path} --model mlx-community/whisper-small-mlx --language ru
  ```
- `.env` не коммитить (добавлен в `.gitignore`)

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

### Локальные проверки

| Команда | Что делает |
|---|---|
| `make check-fast` | compile + focused tests + validate |
| `make check` | то же + full tests + doctor |
| `--verbose` | дополнительно стримить полный вывод в консоль |

```bash
./scripts/check.sh --fast           # быстро, тихо
./scripts/check.sh                  # полный прогон, тихо
./scripts/check.sh --fast --verbose # быстро + полный вывод
./scripts/check.sh --verbose        # полный прогон + полный вывод
```

По умолчанию в консоль выводятся только секции и итог (`✓ / ❌`).
Полный вывод каждой команды (включая WARNING/ERROR из тестов) пишется в:

```
.tmp/check/check-YYYYMMDD-HHMMSS.log
```

При ошибке автоматически показываются последние 80 строк лога.

Скрипт использует `.venv/bin/python` если есть, иначе `python3`. Останавливается на первой ошибке.

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
