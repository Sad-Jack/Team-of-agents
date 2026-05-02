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

## Диагностика

```bash
python3 run.py telegram-config   # показывает конфиг без секретов
python3 run.py doctor             # полная диагностика системы
python3 run.py config             # конфиг LLM-провайдера
```
