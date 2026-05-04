"""Deterministic fast-path router for task/bug creation commands in Telegram.

Detects Russian natural-language creation phrases and creates tasks/bugs
directly through the orchestrator storage layer — without calling any LLM,
Supervisor, or Claude Code.

Public API
----------
detect_create_intent(text) -> ("task"|"bug", title) or None
detect_imperative_create_intent(text) -> ("task", title) or None
create_task_fast(title, description)  -> orchestrator task dict
create_bug_fast(title, description)   -> orchestrator task dict (bug)
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------

_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?]+$")
_MULTI_SPACE_RE = re.compile(r"\s+")


def normalize_title(raw: str) -> str:
    """Normalise a raw title candidate extracted after stripping the intent prefix.

    Rules:
    - Strip leading/trailing whitespace
    - Remove trailing sentence-ending punctuation (. , ; : ! ?)
    - Collapse multiple spaces into one
    - Capitalise the first character (without touching the rest of the string,
      so English words / IDs are preserved)
    """
    t = raw.strip()
    t = _TRAILING_PUNCT_RE.sub("", t).strip()
    t = _MULTI_SPACE_RE.sub(" ", t).strip()
    if t:
        t = t[0].upper() + t[1:]
    return t


# ---------------------------------------------------------------------------
# Intent patterns
# ---------------------------------------------------------------------------
# Each pattern matches the *prefix* of the user's message that indicates a
# creation intent.  The rest of the string (text[match.end():]) becomes the
# raw title candidate.
#
# Pattern ordering: most-specific first to avoid greedy shorter matches.
# ---------------------------------------------------------------------------

# ---------- task patterns ----------
_TASK_PATTERNS: list[re.Pattern] = [
    # "создать задачу ...", "создай задачу ..."
    re.compile(r"^создат[ьи]\s+задач[ую]?\s*", re.IGNORECASE),
    re.compile(r"^создай\s+задач[ую]?\s*", re.IGNORECASE),
    # "добавить задачу ...", "добавь задачу ..."
    re.compile(r"^добавит[ьи]\s+задач[ую]?\s*", re.IGNORECASE),
    re.compile(r"^добавь\s+задач[ую]?\s*", re.IGNORECASE),
    # "поставь задачу ...", "поставить задачу ..."
    re.compile(r"^поставит[ьи]\s+задач[ую]?\s*", re.IGNORECASE),
    re.compile(r"^поставь\s+задач[ую]?\s*", re.IGNORECASE),
    # "новая задача ..."
    re.compile(r"^новая\s+задача\s+", re.IGNORECASE),
    # "надо сделать ...", "нужно сделать ..."
    re.compile(r"^надо\s+сделать\s+", re.IGNORECASE),
    re.compile(r"^нужно\s+сделать\s+", re.IGNORECASE),
    # "задачу на ...", "задачу ..."  (accusative form — e.g. voice STT output)
    re.compile(r"^задач[ую]\s+на\s+", re.IGNORECASE),
    re.compile(r"^задач[ую]\s+", re.IGNORECASE),
    # "задача на ...", "задача ..."  (nominative form, needs content after)
    re.compile(r"^задача\s+на\s+", re.IGNORECASE),
    re.compile(r"^задача\s+", re.IGNORECASE),
]

# ---------- bug patterns ----------
_BUG_PATTERNS: list[re.Pattern] = [
    # "создать баг ...", "создай баг ..."
    re.compile(r"^создат[ьи]\s+баг\s*", re.IGNORECASE),
    re.compile(r"^создай\s+баг\s*", re.IGNORECASE),
    # "добавить баг ...", "добавь баг ..."
    re.compile(r"^добавит[ьи]\s+баг\s*", re.IGNORECASE),
    re.compile(r"^добавь\s+баг\s*", re.IGNORECASE),
    # "новый баг ..."
    re.compile(r"^новый\s+баг\s+", re.IGNORECASE),
    # "нашел баг ...", "нашёл баг ...", "нашла баг ..."
    re.compile(r"^нашел\s+баг\s+", re.IGNORECASE),
    re.compile(r"^нашёл\s+баг\s+", re.IGNORECASE),
    re.compile(r"^нашла\s+баг\s+", re.IGNORECASE),
    # "есть баг ..."
    re.compile(r"^есть\s+баг\s+", re.IGNORECASE),
    # "баг ..."  (bare noun, needs content after)
    re.compile(r"^баг\s+", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Imperative task patterns (no explicit "задачу" word required)
# ---------------------------------------------------------------------------
# These match standalone imperative infinitive verb phrases in Russian.
# The *entire* normalised message becomes the task title (verb included).
# Only used when there is no active focus; callers must enforce that guard.
# ---------------------------------------------------------------------------

_IMPERATIVE_TASK_PATTERNS: list[re.Pattern] = [
    # "добавить ...", "добавь ..." — but NOT "добавить задачу ..." (caught by _TASK_PATTERNS)
    re.compile(r"^добавит[ьи]\s+(?!задач)", re.IGNORECASE),
    # "сделать ..."
    re.compile(r"^сделат[ьи]\s+", re.IGNORECASE),
    # "реализовать ..."
    re.compile(r"^реализоват[ьи]\s+", re.IGNORECASE),
    # "доработать ..."
    re.compile(r"^доработат[ьи]\s+", re.IGNORECASE),
    # "исправить ..."
    re.compile(r"^исправит[ьи]\s+", re.IGNORECASE),
    # "проверить ..."
    re.compile(r"^проверит[ьи]\s+", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Public detection function
# ---------------------------------------------------------------------------

_DetectResult = Optional[tuple[str, str]]   # ("task"|"bug", normalised_title)


def detect_create_intent(text: str) -> _DetectResult:
    """Detect create-task or create-bug intent from a natural-language string.

    Returns ``("task", title)`` or ``("bug", title)`` when a pattern matches,
    where *title* is the normalised title extracted after the prefix.
    Returns ``None`` when the text does not match any known creation pattern.

    The returned *title* may be an empty string if the user wrote only the
    prefix with no content; callers should show a friendly error in that case.

    Note: bug patterns are checked first because some bug phrases ("есть баг")
    would also weakly match task-adjacent words.
    """
    t = text.strip()
    # Bug patterns first (more specific triggers)
    for pattern in _BUG_PATTERNS:
        m = pattern.match(t)
        if m:
            raw = t[m.end():]
            return ("bug", normalize_title(raw))
    # Task patterns
    for pattern in _TASK_PATTERNS:
        m = pattern.match(t)
        if m:
            raw = t[m.end():]
            return ("task", normalize_title(raw))
    return None


def detect_imperative_create_intent(text: str) -> _DetectResult:
    """Detect implicit task creation from a bare imperative-verb message.

    Unlike ``detect_create_intent``, the *entire* normalised message becomes
    the task title (verb is preserved).  Returns ``("task", title)`` when a
    pattern matches, or ``None`` otherwise.

    Callers are responsible for checking whether an active focus exists before
    calling this function — imperative phrases should not hijack focus mode.

    Examples::

        "Добавить CLI-команду task-summary, ..."
            → ("task", "Добавить CLI-команду task-summary, ...")

        "Реализовать OAuth через GitHub"
            → ("task", "Реализовать OAuth через GitHub")
    """
    t = text.strip()
    for pattern in _IMPERATIVE_TASK_PATTERNS:
        if pattern.match(t):
            return ("task", normalize_title(t))
    return None


# ---------------------------------------------------------------------------
# Creation helpers
# ---------------------------------------------------------------------------

_DEFAULT_DESCRIPTION = "Создано из Telegram сообщения."


def create_task_fast(
    title: str,
    description: str = _DEFAULT_DESCRIPTION,
) -> dict:
    """Create a task via the orchestrator without calling any LLM.

    Uses the same storage layer as the normal Supervisor pipeline.
    """
    import orchestrator as _orch  # local import to keep module lightweight

    return _orch.create_task(title, description)


def create_bug_fast(
    title: str,
    description: str = _DEFAULT_DESCRIPTION,
) -> dict:
    """Create a bug via the orchestrator storage layer without calling any LLM.

    Bypasses the ``bug_intake`` agent that ``orchestrator.create_bug`` uses,
    creating a minimal but fully schema-valid bug record directly.
    """
    import orchestrator as _orch  # local import

    tasks = _orch.load_tasks()
    new_bug = _orch._normalize_task_schema(
        {
            "id": _orch._next_id_with_prefix(tasks, "BUG"),
            "type": "bug",
            "title": title,
            "description": description,
            "status": "idea",
            "priority": "medium",
            "severity": "unknown",
            "depends_on": [],
            "blocked_by": [],
            "blocked_reason": "",
            "tags": [],
            "estimate": None,
            "raw_input": "",
            "related_decisions": [],
            "release_id": None,
            "artifacts": {},
            "history": [],
            "notes": [],
        }
    )
    _orch.validate_task(new_bug)
    tasks.append(new_bug)
    _orch.save_tasks(tasks)
    return new_bug
