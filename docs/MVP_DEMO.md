# MVP Demo

## Что уже умеет MVP

- управление задачами и багами
- workflow через роли `Analyst -> Architect -> Developer -> QA`
- backlog и зависимости
- решения ADR
- релизы и readiness
- безопасные команды через allowlist
- Supervisor для natural language
- Telegram интерфейс (polling, owner-only)

## Как проверить готовность

```bash
python3 run.py doctor
python3 -m unittest discover -s tests
```

## Как сбросить и засидировать demo-данные

```bash
python3 run.py demo-reset --yes
python3 run.py demo-seed
```

`demo-reset` разрушительная для demo-данных команда: используйте только с `--yes`.

## Как запустить demo flow

```bash
python3 run.py demo
python3 run.py e2e-demo
```

## Как использовать Supervisor

```bash
python3 run.py supervise --text "Что делать дальше?"
python3 run.py supervise --text "run command python3 run.py validate" --execute
python3 run.py supervise --text "run command python3 run.py validate" --execute --yes
```

## Как использовать Telegram после настройки

1. Проверьте конфиг:
```bash
python3 run.py telegram-config
```
2. Запустите:
```bash
python3 run.py telegram
```
3. Используйте команды `/dryrun`, `/execute`, `/yes`.

## Что намеренно не автоматизировано

- нет базы данных
- нет web server/webhook
- patch не применяется автоматически
- нет деплоя

## Рекомендуемый первый реальный use case

Начните с одной локальной feature-задачи:
1. `create`
2. `attach-repo-context`
3. несколько раз `run-next`
4. `run-command` для валидации
5. `qa-report`
6. `release-readiness`
