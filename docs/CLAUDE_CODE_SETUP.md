# Настройка Claude Code

## 1. Установка и проверка

Установите Claude Code официальным способом и проверьте бинарник:

```bash
claude --help
```

Если бинарник не в PATH, задайте `CLAUDE_CODE_BINARY`.

## 2. Авторизация

Выполните вход в Claude Code через вашу Claude-подписку.

## 3. Настройка провайдера

В `.env`:

```env
LLM_PROVIDER=claude_code
CLAUDE_CODE_BINARY=claude
CLAUDE_CODE_TIMEOUT_SECONDS=120
```

## 4. Проверка

```bash
python3 run.py config
python3 run.py llm-smoke --prompt "Return JSON: {\"ok\": true}"
```

## 5. Важно про ANTHROPIC_API_KEY

Если `ANTHROPIC_API_KEY` установлен, Claude Code может переключиться на API-биллинг
в зависимости от конфигурации/режима авторизации.
Если вы хотите использовать подписку, обычно не задавайте этот ключ.

## 6. Лимиты использования

Работа через Claude Code зависит от ограничений вашей подписки (лимиты, квоты, rate limits).

## 7. Fallback

Для тестов и локальной диагностики без внешних вызовов используйте:

```env
LLM_PROVIDER=fake
```
