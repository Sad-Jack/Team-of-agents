#!/usr/bin/env bash
# check.sh — quality-gate checks for this project.
#
# Usage:
#   ./scripts/check.sh                  # full check
#   ./scripts/check.sh --fast           # compile + focused tests + validate only
#   ./scripts/check.sh --verbose        # stream full output to console (also logged)
#   ./scripts/check.sh --fast --verbose # combine flags in any order
#
# By default only section headers and pass/fail lines are shown.
# Full output of every step is always written to:
#   .tmp/check/check-YYYYMMDD-HHMMSS.log
#
# On failure the last 80 lines of the log are printed automatically.

set -uo pipefail

# ---------------------------------------------------------------------------
# Resolve repo root and cd into it so all paths are relative.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Pick the Python binary: prefer .venv if present.
# ---------------------------------------------------------------------------
if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

# ---------------------------------------------------------------------------
# Parse flags (any order).
# ---------------------------------------------------------------------------
FAST=0
VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        --fast)    FAST=1 ;;
        --verbose) VERBOSE=1 ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: $0 [--fast] [--verbose]" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Set up log file.
# ---------------------------------------------------------------------------
LOG_DIR=".tmp/check"
mkdir -p "${LOG_DIR}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${LOG_DIR}/check-${TIMESTAMP}.log"
: > "${LOG}"   # create / truncate

echo "Log: ${LOG}"

# ---------------------------------------------------------------------------
# Colours (used only on the console, not in the log).
# ---------------------------------------------------------------------------
BOLD='\033[1m'
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
section() {
    local title="$1"
    # Console
    echo ""
    echo -e "${CYAN}${BOLD}=== ${title} ===${RESET}"
    # Log: plain header so the log is easy to grep
    echo "" >> "${LOG}"
    echo "### ${title} ###" >> "${LOG}"
}

ok() {
    echo -e "${GREEN}✓ $1${RESET}"
    echo "OK: $1" >> "${LOG}"
}

# run_step <display-name> <cmd> [args...]
#
# Runs the command, captures output to LOG.
# In verbose mode also streams to the console via tee.
# On failure: prints error + last 80 log lines, exits 1.
run_step() {
    local name="$1"
    shift
    local rc=0

    if [[ "${VERBOSE}" == "1" ]]; then
        # Stream output to both console and log simultaneously.
        # PIPESTATUS[0] captures the exit code of the real command past the pipe.
        "$@" 2>&1 | tee -a "${LOG}" || true
        rc="${PIPESTATUS[0]}"
    else
        # Quiet: capture everything in the log only.
        "$@" >> "${LOG}" 2>&1 || rc=$?
    fi

    if [[ "${rc}" -ne 0 ]]; then
        echo -e "\n${RED}${BOLD}❌ ${name} failed${RESET}" >&2
        echo "Full log: ${LOG}" >&2
        echo "" >&2
        echo "Last 80 lines:" >&2
        tail -80 "${LOG}" >&2
        exit 1
    fi

    ok "${name} passed"
}

# ---------------------------------------------------------------------------
# Steps.
# ---------------------------------------------------------------------------

# 1. Compile
section "compile"
run_step "compile" \
    "${PYTHON}" -m compileall \
        run.py \
        telegram_board.py \
        telegram_bot.py \
        tests/test_telegram_board.py \
        tests/test_telegram_bot.py \
        -q

# 2. Focused tests
section "focused tests"
run_step "focused tests" \
    "${PYTHON}" -m unittest tests.test_telegram_board tests.test_telegram_bot

# 3. Validate
section "validate"
run_step "validate" \
    "${PYTHON}" run.py validate

# --- fast mode stops here ---
if [[ "${FAST}" == "1" ]]; then
    echo ""
    echo -e "${GREEN}${BOLD}✅ Fast check passed (compile · focused tests · validate)${RESET}"
    echo "Full log: ${LOG}"
    exit 0
fi

# 4. Full tests
section "full tests"
run_step "full tests" \
    "${PYTHON}" -m unittest discover -s tests

# 5. Doctor
section "doctor"
run_step "doctor" \
    "${PYTHON}" run.py doctor

# ---------------------------------------------------------------------------
# Done.
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}✅ All checks passed (compile · focused tests · validate · full tests · doctor)${RESET}"
echo "Full log: ${LOG}"
