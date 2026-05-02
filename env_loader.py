from __future__ import annotations

import os


def load_dotenv_if_exists(path: str = ".env", override: bool = False) -> dict:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    - Missing file is silently ignored.
    - Lines starting with # are comments and are skipped.
    - Empty lines are skipped.
    - Values wrapped in single or double quotes have the quotes stripped.
    - If override=False (default), existing env vars are not overwritten.
    - Returns a dict of the key/value pairs that were loaded from the file
      (does not include pairs that were skipped because of override=False).
    - Values are never printed to stdout or stderr.
    """
    loaded: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return loaded
    except OSError:
        return loaded

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key:
            continue

        # Strip inline comment (only outside quotes)
        value = _strip_inline_comment(raw_value)
        value = _strip_quotes(value)

        if not override and key in os.environ:
            continue

        os.environ[key] = value
        loaded[key] = value

    return loaded


def _strip_inline_comment(value: str) -> str:
    """Remove trailing # comment from an unquoted value."""
    stripped = value.strip()
    if stripped and stripped[0] in ('"', "'"):
        return stripped
    idx = stripped.find(" #")
    if idx >= 0:
        return stripped[:idx].strip()
    return stripped


def _strip_quotes(value: str) -> str:
    """Remove surrounding single or double quotes from a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value
