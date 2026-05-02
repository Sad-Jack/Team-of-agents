# Контекст диалога и активный фокус

## Зачем это нужно
- Не повторять `TASK-...`/`REL-...`/`ADR-...` в каждом сообщении.
- Вести разговор как с Project Manager: сначала установить фокус, потом давать follow-up команды.

## Модель сессии
- Сессии хранятся в коллекции `sessions`.
- Для CLI по умолчанию используется `cli:default`.
- Для Telegram используется `telegram:<user_id>`.

Поля сессии:
- `active_task_id`
- `active_release_id`
- `active_decision_id`
- `recent_messages` (только последние 20 сообщений)

## CLI команды
- `python3 run.py focus`
- `python3 run.py focus-task --id TASK-1`
- `python3 run.py focus-release --id REL-001`
- `python3 run.py focus-decision --id ADR-001`
- `python3 run.py clear-focus`
- `python3 run.py sessions`
- `python3 run.py session --id telegram:123456789`

## Telegram
- `/focus`
- `/focus_task TASK-1`
- `/focus_release REL-001`
- `/focus_decision ADR-001`
- `/clear_focus`

## Примеры
1. `Обсудим TASK-1`
2. `Добавь заметку: проверить edge case`
3. `Подготовь её к разработке`
4. `Что по ней сейчас?`

## Ограничения
- Фокус помогает только с явными follow-up запросами.
- Если фокус не установлен и ID не указан, Supervisor попросит уточнение.
