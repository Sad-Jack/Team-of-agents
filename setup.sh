#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Создаю виртуальное окружение .venv..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "    .venv создано."
else
    echo "    .venv уже существует, пропускаю."
fi

# shellcheck source=/dev/null
source .venv/bin/activate

echo "==> Обновляю pip..."
pip install --upgrade pip --quiet

echo "==> Устанавливаю зависимости из requirements.txt..."
pip install -r requirements.txt --quiet

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo ""
        echo "==> Создан .env из .env.example."
        echo "    Заполни обязательные переменные:"
        echo "      TELEGRAM_BOT_TOKEN=<токен от @BotFather>"
        echo "      TELEGRAM_OWNER_ID=<твой Telegram user id>"
    else
        echo ""
        echo "==> .env.example не найден. Создай .env вручную."
        echo "    Минимальный набор:"
        echo "      TELEGRAM_BOT_TOKEN=<токен от @BotFather>"
        echo "      TELEGRAM_OWNER_ID=<твой Telegram user id>"
    fi
else
    echo "==> .env уже существует, не перезаписываю."
fi

echo ""
echo "Готово. Следующие шаги:"
echo "  1. Проверь .env и заполни TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_ID"
echo "  2. Проверь конфиг:  python3 run.py telegram-config"
echo "  3. Запусти doctor:  python3 run.py doctor"
echo "  4. Запусти бота:    ./start.sh"
