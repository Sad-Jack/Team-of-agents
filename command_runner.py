import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone

ALLOWED_COMMANDS = {
    "python -m unittest discover -s tests",
    "python3 -m unittest discover -s tests",
    "python run.py validate",
    "python3 run.py validate",
    "python run.py agents",
    "python3 run.py agents",
    "python run.py config",
    "python3 run.py config",
    "python run.py context",
    "python3 run.py context",
    "python run.py list",
    "python3 run.py list",
}

FORBIDDEN_TOKENS = ["&&", "||", ";", "|", ">", "<", "`", "$("]
MAX_OUTPUT = 10000


def is_command_allowed(command: str) -> bool:
    if not isinstance(command, str):
        return False
    text = command.strip()
    if not text:
        return False
    if any(token in text for token in FORBIDDEN_TOKENS):
        return False
    return text in ALLOWED_COMMANDS


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trim(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + "\n...[truncated]"


def run_safe_command(command: str, cwd: str = ".", timeout_seconds: int = 30) -> dict:
    cmd = command.strip() if isinstance(command, str) else ""
    if not cmd:
        raise ValueError("Command is empty.")
    if timeout_seconds > 120:
        raise ValueError("timeout_seconds must be <= 120.")
    if not is_command_allowed(cmd):
        raise ValueError(f"Command is not allowed: {cmd}")

    started_at = _utc_now()
    start = time.monotonic()

    try:
        args = shlex.split(cmd)
        if args and args[0] == "python" and shutil.which("python") is None and shutil.which("python3"):
            args[0] = "python3"
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
            check=False,
        )
        end = time.monotonic()
        stdout = _trim(completed.stdout or "")
        stderr = _trim(completed.stderr or "")
        exit_code = completed.returncode
        success = exit_code == 0
        finished_at = _utc_now()
        return {
            "command": cmd,
            "exit_code": exit_code,
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(end - start, 3),
            "source": "manual",
            "working_directory": cwd,
        }
    except subprocess.TimeoutExpired:
        end = time.monotonic()
        finished_at = _utc_now()
        return {
            "command": cmd,
            "exit_code": None,
            "success": False,
            "stdout": "",
            "stderr": f"Command timed out after {timeout_seconds} seconds.",
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(end - start, 3),
            "source": "manual",
            "working_directory": cwd,
        }
