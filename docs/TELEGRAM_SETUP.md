# Настройка Telegram Bot

## Быстрая настройка

```bash
./setup.sh        # создаёт .venv, устанавливает зависимости, копирует .env.example
```

Заполни `.env`:

```env
TELEGRAM_BOT_TOKEN=<токен от @BotFather>
TELEGRAM_OWNER_ID=<твой Telegram user id>
```

Проверь:

```bash
python3 run.py telegram-config
python3 run.py doctor
```

Запусти:

```bash
./start.sh
```

---

## Получить токен и owner id

1. **TELEGRAM_BOT_TOKEN** — создай бота через `@BotFather` в Telegram, команда `/newbot`.
2. **TELEGRAM_OWNER_ID** — узнай свой user id через `@userinfobot` или `@getmyid_bot`.

---

## Переменные окружения

`.env` подхватывается автоматически при запуске `python3 run.py ...`.
Вручную делать `source .env` или `set -a; source .env; set +a` больше не нужно.

| Переменная | Обязательна | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | токен бота от @BotFather |
| `TELEGRAM_OWNER_ID` | да | твой Telegram user id |
| `TELEGRAM_DRY_RUN_BY_DEFAULT` | нет | `true` (по умолчанию) — plain text только планирует действие |
| `LLM_PROVIDER` | нет | `fake` (по умолчанию), `openai`, `claude_code` |

Полный список переменных смотри в `.env.example`.

---

## Как работает .env

`run.py` при старте вызывает `env_loader.load_dotenv_if_exists()`:
- если `.env` не существует — ничего не происходит
- реальные переменные окружения имеют **приоритет** над `.env` (override=False)
- значения из `.env` не печатаются в логах

---

## Запуск бота

### Через start.sh (рекомендуется)

```bash
./start.sh
```

Скрипт:
1. Активирует `.venv`
2. Запускает `python3 run.py telegram`

### Напрямую (если venv уже активирован)

```bash
python3 run.py telegram
```

### Через make

```bash
make start
```

---

## Безопасность

- owner-only: бот отвечает только пользователю с `TELEGRAM_OWNER_ID`
- по умолчанию dry-run (`TELEGRAM_DRY_RUN_BY_DEFAULT=true`): plain text только планирует
- рискованные действия (run_command, apply_patch и др.) требуют `/yes ...`
- токен никогда не печатается в логах и выводе команд
- `.env` нельзя коммитить — добавь в `.gitignore`

---

## Голосовые сообщения

По умолчанию отключены (`STT_PROVIDER=disabled`). Чтобы включить:

```env
STT_PROVIDER=whisper_cli
FFMPEG_BINARY=ffmpeg
WHISPER_CLI_BINARY=whisper
WHISPER_MODEL=small
WHISPER_LANGUAGE=ru
```

Проверка: `python3 run.py voice-config`

---

## Подтверждение действий кнопками

При `TELEGRAM_DRY_RUN_BY_DEFAULT=true` (по умолчанию) каждый plain-text запрос показывает план + inline-кнопки:

```
Plan: create_task ...
[✅ Выполнить]  [❌ Отмена]
```

- **✅ Выполнить** — выполняет действие без дополнительных подтверждений, если оно не является рискованным
- **❌ Отмена** — отменяет pending action и удаляет его из памяти
- Рискованные действия (`apply_patch`, `run_command`, `run_all` и др.) через кнопку не выполняются — требуют явного `/yes <запрос>`

Хранится только **последний** pending action на сессию. Новый dry-run заменяет предыдущий.

---

## Status chat для событий агентов

Добавь в `.env`:

```env
TELEGRAM_STATUS_CHAT_ID=<id чата или канала>
TELEGRAM_NOTIFY_AGENT_EVENTS=true
TELEGRAM_NOTIFY_TASK_EVENTS=true
```

### Как получить TELEGRAM_STATUS_CHAT_ID

**Для личного чата:**
1. Напиши боту любое сообщение
2. Открой `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Найди `"chat":{"id":...}` — это и есть твой chat_id

**Для группы или канала:**
1. Добавь бота в группу/канал как администратора
2. Отправь сообщение в группу
3. Получи chat_id через `/getUpdates` (у групп/каналов — отрицательный id)

**Через @userinfobot:**
- Переслать сообщение из нужного чата боту `@userinfobot`

### Карточки задач и Reply-based управление

Когда задача или баг создаётся через Telegram, бот отправляет карточку в status chat:

```
🧩 TASK-12: Add healthcheck command
Статус: idea
Приоритет: medium
Описание: Add a healthcheck endpoint to verify system health.
—
Ответь на это сообщение, чтобы работать с задачей.
```

**Ответь на карточку** — и бот поймёт контекст задачи без явного указания ID:

| Что написать | Что произойдёт |
|---|---|
| `бери в работу` | Supervisor готовит задачу к разработке |
| `доработай описание` | Обновляет описание задачи |
| `что по этой задаче?` | Показывает статус и детали |
| `добавь заметку: нужно проверить на Mac` | Добавляет заметку к задаче |

Бот разрешает message_id → task_id **локально** (без LLM-вызова) — только после этого идёт к Supervisor. Это сохраняет токены для задач с заранее известным контекстом.

### Остальные уведомления

| Событие | Сообщение |
|---|---|
| run_next / advance | `✅ Работа по TASK-X завершена\nНовый статус: ...` |
| Ошибка выполнения | `❌ Ошибка при выполнении действия\nAction: ...\nПричина: ...` |

Если `TELEGRAM_STATUS_CHAT_ID` не задан — уведомления и карточки не отправляются, основной flow работает как обычно.
Если отправка не удалась — основной flow не прерывается, ошибка логируется.

### Хранение связей сообщений

Связи `Telegram message_id → task_id` хранятся в:
```
sessions/telegram_message_links.json
```

Файл создаётся автоматически. Не содержит секретов.
Формат: список объектов `{telegram_chat_id, telegram_message_id, work_item_type, work_item_id, created_at}`.

---

## /start и изучение проекта

`/start` показывает:
- текущий режим работы (self-managed / embedded)
- system root и managed repo root
- предупреждение, если бот работает над самим Team-of-agents
- кнопку **🔍 Изучить проект**

### Кнопка "Изучить проект"

Запускает безопасный readonly-scan:
1. Проверяет `MANAGED_REPO_PATH` и managed repo root
2. Запускает `scan_repository()` — только read, без изменений
3. Возвращает: количество файлов, предупреждения, подсказки по следующим шагам

Не выполняет никаких рискованных действий (нет patch, нет команд).

---

## Self mode vs Embedded mode

| | Self mode | Embedded mode |
|---|---|---|
| `MANAGED_REPO_PATH` | `.` | `..` (или другой путь) |
| Бот управляет | самим Team-of-agents | внешним проектом |
| repo scan | работает над Team-of-agents | работает над внешним проектом |
| Предупреждение в /start | да | нет |

**Чтобы переключиться на embedded mode:**

```env
MANAGED_REPO_PATH=..
```

Типовая структура:
```
my-project/
  src/
  tests/
  team-agents/   ← здесь находится Team-of-agents
    .env         ← MANAGED_REPO_PATH=..
```

---

## Telegram Board / Admin Room

Forum-группа используется как проектная доска — только для отображения карточек.
Работа с задачами ведётся **только в приватном чате**.

### Переменные окружения Board

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOARD_ENABLED` | `true` чтобы включить. По умолчанию `false`. |
| `TELEGRAM_BOARD_CHAT_ID` | ID форум-группы (отрицательное число). |
| `TELEGRAM_TOPIC_TASK_IDEAS` | message_thread_id топика "Task Ideas" |
| `TELEGRAM_TOPIC_TASK_READY` | message_thread_id топика "Task Ready" |
| `TELEGRAM_TOPIC_TASK_ACTIVE` | message_thread_id топика "Task Active" |
| `TELEGRAM_TOPIC_TASK_BLOCKED` | message_thread_id топика "Task Blocked" |
| `TELEGRAM_TOPIC_BUGS_NEW` | message_thread_id топика "Bugs New" |
| `TELEGRAM_TOPIC_BUGS_ACTIVE` | message_thread_id топика "Bugs Active" |
| `TELEGRAM_TOPIC_NEEDS_INPUT` | message_thread_id топика "Needs Input" |
| `TELEGRAM_TOPIC_RELEASES` | message_thread_id топика "Releases" |
| `TELEGRAM_TOPIC_AGENT_LOG` | message_thread_id топика "Agent Log" |
| `TELEGRAM_TOPIC_DECISIONS` | message_thread_id топика "Decisions" |

### Как получить значения из ссылки на топик

Ссылка на топик: `t.me/c/3952202151/10`

Означает:
```env
TELEGRAM_BOARD_CHAT_ID=-1003952202151   # добавь -100 перед id из ссылки
TELEGRAM_TOPIC_RELEASES=10              # число после последнего /
```

### Проверить конфигурацию

```bash
/board_config    # в Telegram: показывает статус без секретов
python3 run.py telegram-config   # в консоли: все флаги *_SET без значений
```

Подробнее об архитектуре Board: [TELEGRAM_BOARD.md](TELEGRAM_BOARD.md)

---

## Диагностика

```bash
python3 run.py telegram-config   # показывает конфиг без секретов
python3 run.py doctor             # полная диагностика системы
python3 run.py config             # конфиг LLM-провайдера
```
