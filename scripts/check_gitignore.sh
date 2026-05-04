#!/usr/bin/env bash
# check_gitignore.sh — verify that all runtime/state files are properly ignored by git.
#
# Usage:
#   ./scripts/check_gitignore.sh          # exits 0 on pass, 1 on failure
#   ./scripts/check_gitignore.sh --quiet  # suppress per-file output
#
# Run from the repository root.

set -euo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

PASS=0
FAIL=0
ERRORS=()

check_ignored() {
    local file="$1"
    # git check-ignore exits 0 when the file IS ignored, 1 when it is not
    if git check-ignore -q "$file" 2>/dev/null; then
        [[ $QUIET -eq 0 ]] && echo "  ✅  $file"
        ((PASS++)) || true
    else
        [[ $QUIET -eq 0 ]] && echo "  ❌  $file  ← NOT ignored!"
        ERRORS+=("$file")
        ((FAIL++)) || true
    fi
}

echo "=== Gitignore runtime-state check ==="
echo ""

# --- Task / release / decision JSON state ------------------------------------
check_ignored "tasks/tasks.json"
check_ignored "releases/releases.json"
check_ignored "decisions/index.json"

# --- Session state -----------------------------------------------------------
check_ignored "sessions/sessions.json"
check_ignored "sessions/telegram_message_links.json"

# --- SQLite database ---------------------------------------------------------
check_ignored "data/team_agents.db"
check_ignored "data/team_agents.sqlite"

# --- Temporary / CI artefacts ------------------------------------------------
check_ignored ".tmp/check/test.log"
check_ignored ".tmp/voice/recording.wav"

# --- Logs --------------------------------------------------------------------
check_ignored "logs/errors/TG-20260101-120000-abcd.log"

# --- Generated artefacts (create a temp probe file, clean up after) ----------
PROBE="artifacts/TASK-999/probe.txt"
mkdir -p "$(dirname "$PROBE")"
touch "$PROBE"
check_ignored "$PROBE"
rm -f "$PROBE"
rmdir "artifacts/TASK-999" 2>/dev/null || true

# --- Telegram runtime mappings -----------------------------------------------
check_ignored "sessions/telegram_message_links.json"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="

if [[ ${FAIL} -gt 0 ]]; then
    echo ""
    echo "FAIL — the following files are NOT properly ignored:"
    for f in "${ERRORS[@]}"; do
        echo "  • $f"
    done
    echo ""
    echo "Fix: add matching pattern to .gitignore and run:"
    echo "  git rm --cached <file>   # stop tracking the file"
    exit 1
fi

echo ""
echo "OK — all runtime/state files are properly ignored by git."
exit 0
