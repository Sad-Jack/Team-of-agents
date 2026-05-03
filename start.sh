#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Виртуальное окружение .venv не найдено."
    echo "Создай его:"
    echo "  python3 -m venv .venv && source .venv/bin/activate && python3 -m pip install -r requirements.txt"
    exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

exec python3 run.py telegram
