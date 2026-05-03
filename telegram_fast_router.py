"""
Local fast router for Telegram bot.

Matches common natural-language queries in Russian without calling the LLM/Supervisor.
Returns a (intent, handler_fn) pair when a pattern matches, or None to fall through
to the normal Supervisor path.

Controlled by TELEGRAM_FAST_ROUTER_ENABLED (default: true).
"""
from __future__ import annotations

import os
import re
import logging
from typing import Callable

import backlog as _backlog
import orchestrator as _orchestrator
from project_manager import (
    get_blockers_summary,
    get_next_work_recommendation,
    get_project_status,
)

# ---------------------------------------------------------------------------
# Env flag
# ---------------------------------------------------------------------------

def is_fast_router_enabled() -> bool:
    v = (os.getenv("TELEGRAM_FAST_ROUTER_ENABLED") or "true").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[?!.,;:\"'(){}\[\]]+")


def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip safe punctuation."""
    text = text.lower().strip()
    text = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Action-verb guard  — any of these tokens means the message is state-changing;
# return None immediately and let the Supervisor handle it.
# ---------------------------------------------------------------------------

_ACTION_VERBS: frozenset[str] = frozenset({
    # imperative 2nd-person singular (most common form in chat)
    "создай", "добавь", "измени", "удали", "запусти", "выполни",
    "сделай", "почини", "зафиксируй", "обнови", "запиши", "примени",
    "бери", "возьми", "запланируй", "составь", "назначь", "установи",
    "подготовь", "прикрепи", "отмени", "закрой", "реши", "исправь",
    "напиши", "реализуй", "открой", "сгенерируй", "опубликуй", "выпусти",
    "слинкуй", "прими", "одобри",
    # infinitives (users sometimes write them)
    "создать", "добавить", "изменить", "удалить", "запустить", "выполнить",
    "сделать", "починить", "зафиксировать", "обновить", "записать",
    "применить", "взять", "запланировать", "составить", "назначить",
    "установить", "подготовить", "прикрепить", "отменить", "закрыть",
    "решить", "исправить", "написать", "реализовать",
})


def _has_action_verb(norm: str) -> bool:
    """Return True if any token in the normalised text is a known action verb."""
    return bool(set(norm.split()) & _ACTION_VERBS)


# ---------------------------------------------------------------------------
# Pattern tables  (read-only intents only)
# ---------------------------------------------------------------------------

_PROJECT_STATUS_PATTERNS = [
    "статус",
    "статус проекта",
    "что по проекту",
    "как там проект",
    "что сейчас происходит",
    "что происходит",
    "как проект",
    "как дела",
    "как дела с проектом",
    "состояние проекта",
    "общий статус",
    "дай статус",
    "покажи статус",
    "покажи статус проекта",
    "текущий статус",
    "общая картина",
]

_BACKLOG_PATTERNS = [
    "покажи задачи",
    "покажи бэклог",
    "покажи backlog",
    "что в бэклоге",
    "какие задачи есть",
    "список задач",
    "все задачи",
    "бэклог",
    "backlog",
    "задачи",
    "какие есть задачи",
    "показать задачи",
    "покажи список задач",
]

_NEXT_ACTION_PATTERNS = [
    "что дальше",
    "что делать дальше",
    "что дальше делать",
    "какой следующий шаг",
    "следующая задача",
    "что взять в работу",
    "с чего начать",
    "следующий шаг",
    "куда двигаться",
    "с чего начинать",
    "что нужно делать",
]

# Bug patterns: only unambiguous read-only phrases (no single bare nouns).
# Single words like "баги" / "ошибки" are kept but the action-verb guard
# prevents "почини баг" or "зафиксируй ошибку" from matching.
_BUGS_PATTERNS = [
    "покажи баги",
    "какие баги",
    "что с багами",
    "список багов",
    "баги",
    "что по багам",
    "покажи ошибки",
    "какие ошибки",
    "список ошибок",
]

_HELP_PATTERNS = [
    "что ты умеешь",
    "помощь",
    "как тобой пользоваться",
    "что можно делать",
    "справка",
    "как пользоваться",
    "что умеешь",
    "инструкция",
]

# Build a flat lookup: normalised pattern -> intent name
_PATTERN_MAP: dict[str, str] = {}
for _pat in _PROJECT_STATUS_PATTERNS:
    _PATTERN_MAP[_pat] = "project_status"
for _pat in _BACKLOG_PATTERNS:
    _PATTERN_MAP[_pat] = "backlog"
for _pat in _NEXT_ACTION_PATTERNS:
    _PATTERN_MAP[_pat] = "next_action"
for _pat in _BUGS_PATTERNS:
    _PATTERN_MAP[_pat] = "bugs"
for _pat in _HELP_PATTERNS:
    if _pat not in _PATTERN_MAP:
        _PATTERN_MAP[_pat] = "help"


# ---------------------------------------------------------------------------
# Keyword fallback (for partial / paraphrased phrases not in the exact table)
# ---------------------------------------------------------------------------

_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["статус", "проект"], "project_status"),
    (["состояние", "проект"], "project_status"),
    (["бэклог"], "backlog"),
    (["backlog"], "backlog"),
    (["список", "задач"], "backlog"),
    (["следующ", "шаг"], "next_action"),
    (["следующ", "задач"], "next_action"),
    (["дальше", "делать"], "next_action"),
    (["делать", "дальше"], "next_action"),
    (["помощ"], "help"),
    (["умеешь"], "help"),
    (["справка"], "help"),
]


def _match_intent(text: str) -> str | None:
    """Return intent name or None. Exact match first, then keyword heuristics."""
    norm = _normalise(text)

    # Reject state-changing messages before any pattern matching.
    if _has_action_verb(norm):
        return None

    # 1. Exact lookup
    if norm in _PATTERN_MAP:
        return _PATTERN_MAP[norm]

    # 2. Keyword heuristics — ALL keywords in rule must be present as substrings
    for keywords, intent in _KEYWORD_RULES:
        if all(kw in norm for kw in keywords):
            return intent

    return None


# ---------------------------------------------------------------------------
# Response formatters  (clean Russian, no technical fields)
# ---------------------------------------------------------------------------

_STATUS_LABELS: dict[str, str] = {
    "idea": "Идеи",
    "refined": "Refined",
    "ready_for_dev": "Готово к разработке",
    "in_progress": "В работе",
    "review": "На ревью",
    "done": "Готово",
}


def _fmt_project_status() -> str:
    try:
        s = get_project_status()
    except Exception as exc:
        logging.exception("fast_router: get_project_status failed")
        return f"⚠️ Не смог получить статус проекта: {exc}"

    total = s.get("total_tasks", 0)
    by_status = s.get("by_status", {})
    blocked = s.get("blocked_tasks_count", 0)
    ready = s.get("ready_tasks_count", 0)

    lines = ["📌 Статус проекта", ""]
    lines.append(f"Задач всего: {total}")

    for key, label in _STATUS_LABELS.items():
        count = by_status.get(key, 0)
        if count:
            lines.append(f"  {label}: {count}")

    bugs = s.get("by_type", {}).get("bug", 0)
    if bugs:
        lines.append(f"  Багов: {bugs}")

    lines.append(f"Готово к работе: {ready}")
    lines.append(f"Заблокировано: {blocked}" if blocked else "Блокеров: нет")

    next_rec = s.get("next_recommendation") or {}
    next_task = next_rec.get("next_task")
    if next_task:
        lines.append("")
        lines.append("Следующий шаг:")
        lines.append(f"  {next_task.get('id')}: {next_task.get('title')}")
    elif blocked:
        lines.append("")
        lines.append("⚠️ Нет свободных задач — есть блокеры.")

    top_blockers = s.get("top_blockers", [])
    if top_blockers:
        lines.append("")
        lines.append("Блокеры:")
        for b in top_blockers[:3]:
            reason = b.get("blocked_reason") or "нет деталей"
            lines.append(f"  • {b.get('id')}: {reason}")

    return "\n".join(lines)


def _fmt_backlog() -> str:
    try:
        tasks = _orchestrator.list_tasks()
    except Exception as exc:
        logging.exception("fast_router: list_tasks failed")
        return f"⚠️ Не смог загрузить задачи: {exc}"

    if not tasks:
        return "📋 Задач нет. Создай первую задачу: «Создай задачу: ...»"

    lines = [f"📋 Задачи ({len(tasks)})", ""]
    for task in tasks:
        tid = task.get("id", "?")
        title = task.get("title", "?")
        status = task.get("status", "?")
        label = _STATUS_LABELS.get(status, status)
        ttype = task.get("type", "")
        icon = "🐞" if ttype == "bug" else "🧩"
        lines.append(f"{icon} {tid}: {title}  [{label}]")

    return "\n".join(lines)


def _fmt_next_action() -> str:
    try:
        rec = get_next_work_recommendation()
    except Exception as exc:
        logging.exception("fast_router: get_next_work_recommendation failed")
        return f"⚠️ Не смог определить следующий шаг: {exc}"

    task = rec.get("next_task")
    if task is None:
        blocked = rec.get("blocked_count", 0)
        msg = "✅ Нет задач для работы."
        if blocked:
            msg += f"\n⚠️ Заблокировано: {blocked}. Сначала устрани блокеры."
        return msg

    tid = task.get("id", "?")
    title = task.get("title", "?")
    status = _STATUS_LABELS.get(task.get("status", ""), task.get("status", "?"))
    priority = task.get("priority", "medium")
    description = (task.get("description") or "").strip()

    lines = [
        "▶️ Следующий шаг",
        "",
        f"{tid}: {title}",
        f"Статус: {status}  |  Приоритет: {priority}",
    ]
    if description:
        lines.append("")
        lines.append(description[:300])
    lines.append("")
    lines.append(f"Чтобы взять в работу: «Подготовь {tid} к разработке»")
    return "\n".join(lines)


def _fmt_bugs() -> str:
    try:
        tasks = _orchestrator.list_tasks()
    except Exception as exc:
        logging.exception("fast_router: list_tasks (bugs) failed")
        return f"⚠️ Не смог загрузить задачи: {exc}"

    bugs = [t for t in tasks if t.get("type") == "bug"]
    if not bugs:
        return "🐞 Открытых багов нет."

    lines = [f"🐞 Баги ({len(bugs)})", ""]
    for bug in bugs:
        bid = bug.get("id", "?")
        title = bug.get("title", "?")
        status = _STATUS_LABELS.get(bug.get("status", ""), bug.get("status", "?"))
        severity = bug.get("severity", "")
        sev_label = f"  [{severity}]" if severity and severity != "unknown" else ""
        lines.append(f"• {bid}: {title}  [{status}]{sev_label}")

    return "\n".join(lines)


def _fmt_help() -> str:
    return (
        "🤖 Что я умею:\n\n"
        "Просто пиши обычным языком:\n"
        "• «Создай задачу: название» — создать новую задачу\n"
        "• «У нас баг: описание» — зафиксировать баг\n"
        "• «статус проекта» — общая картина\n"
        "• «покажи задачи» — список всех задач\n"
        "• «что делать дальше?» — следующий шаг\n"
        "• «покажи баги» — список багов\n"
        "• «подготовь TASK-1 к разработке» — подготовить задачу\n"
        "• «бери в работу» (ответ на карточку задачи) — взять задачу в работу\n\n"
        "Slash-команды:\n"
        "/status  /dryrun  /execute  /yes  /focus  /help"
    )


_HANDLERS: dict[str, Callable[[], str]] = {
    "project_status": _fmt_project_status,
    "backlog": _fmt_backlog,
    "next_action": _fmt_next_action,
    "bugs": _fmt_bugs,
    "help": _fmt_help,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def try_route(user_text: str) -> str | None:
    """
    Try to handle *user_text* locally.

    Returns a formatted reply string if the intent is recognised,
    or None to signal the caller should fall through to the Supervisor.
    """
    if not is_fast_router_enabled():
        return None

    intent = _match_intent(user_text)
    if intent is None:
        return None

    handler = _HANDLERS.get(intent)
    if handler is None:
        return None

    logging.debug("fast_router: matched intent=%s for text=%r", intent, user_text)
    return handler()
