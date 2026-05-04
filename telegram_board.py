"""
Telegram Board — foundation layer.

Provides:
  - BoardTopic enum (topic slots in the forum-group board)
  - BoardConfig dataclass with topic id mapping
  - load_board_config_from_env() — reads TELEGRAM_BOARD_* env vars
  - topic_for_* routing helpers
  - Human-readable card formatters (pure functions, no I/O)

Functional API (simple, env-backed):
  - is_board_enabled() -> bool
  - get_board_chat_id() -> str | None
  - get_topic_id(topic_key) -> int | None
  - get_board_config_status() -> dict
  - format_board_config_status() -> str
  - async send_board_message(context, topic_key, text, reply_markup) -> int | None

No inline buttons in this layer.
No reply-to routing.
Local storage is the source of truth; this module only formats, maps, and publishes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Topic slots
# ---------------------------------------------------------------------------

class BoardTopic(Enum):
    task_ideas   = "task_ideas"
    task_ready   = "task_ready"
    task_active  = "task_active"
    task_blocked = "task_blocked"
    bugs_new     = "bugs_new"
    bugs_active  = "bugs_active"
    needs_input  = "needs_input"
    releases     = "releases"
    agent_log    = "agent_log"
    decisions    = "decisions"


# ---------------------------------------------------------------------------
# Board config
# ---------------------------------------------------------------------------

@dataclass
class BoardConfig:
    enabled: bool = False
    board_chat_id: Optional[str] = None
    # topic slot -> Telegram message_thread_id (int) or None if not configured
    topic_ids: dict[BoardTopic, Optional[int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def topic_id(self, topic: BoardTopic) -> Optional[int]:
        """Return the message_thread_id for a topic, or None if not set."""
        return self.topic_ids.get(topic)

    def is_topic_configured(self, topic: BoardTopic) -> bool:
        return self.topic_ids.get(topic) is not None


# Env-var name for each topic slot
_TOPIC_ENV: dict[BoardTopic, str] = {
    BoardTopic.task_ideas:   "TELEGRAM_TOPIC_TASK_IDEAS",
    BoardTopic.task_ready:   "TELEGRAM_TOPIC_TASK_READY",
    BoardTopic.task_active:  "TELEGRAM_TOPIC_TASK_ACTIVE",
    BoardTopic.task_blocked: "TELEGRAM_TOPIC_TASK_BLOCKED",
    BoardTopic.bugs_new:     "TELEGRAM_TOPIC_BUGS_NEW",
    BoardTopic.bugs_active:  "TELEGRAM_TOPIC_BUGS_ACTIVE",
    BoardTopic.needs_input:  "TELEGRAM_TOPIC_NEEDS_INPUT",
    BoardTopic.releases:     "TELEGRAM_TOPIC_RELEASES",
    BoardTopic.agent_log:    "TELEGRAM_TOPIC_AGENT_LOG",
    BoardTopic.decisions:    "TELEGRAM_TOPIC_DECISIONS",
}


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_topic_id(raw: str | None, env_name: str) -> tuple[Optional[int], Optional[str]]:
    """
    Parse a topic id string into int.
    Returns (int_id, None) on success, (None, warning_message) on parse failure,
    (None, None) when raw is empty/missing.
    """
    if not raw or not raw.strip():
        return None, None
    try:
        return int(raw.strip()), None
    except ValueError:
        return None, f"{env_name}={raw!r} is not a valid integer topic id — ignored"


def load_board_config_from_env() -> BoardConfig:
    """
    Read all TELEGRAM_BOARD_* variables from the environment and return a BoardConfig.

    Rules:
    - TELEGRAM_BOARD_ENABLED defaults to false.
    - Invalid (non-integer) topic ids produce a warning and are skipped; app does not crash.
    - No secret values are stored beyond board_chat_id (which is never printed).
    """
    enabled = _parse_bool(os.getenv("TELEGRAM_BOARD_ENABLED"), default=False)
    board_chat_id = (os.getenv("TELEGRAM_BOARD_CHAT_ID") or "").strip() or None

    warnings: list[str] = []
    topic_ids: dict[BoardTopic, Optional[int]] = {}

    for topic, env_name in _TOPIC_ENV.items():
        raw = os.getenv(env_name)
        tid, warn = _parse_topic_id(raw, env_name)
        topic_ids[topic] = tid
        if warn:
            warnings.append(warn)
            logging.warning("telegram_board: %s", warn)

    cfg = BoardConfig(
        enabled=enabled,
        board_chat_id=board_chat_id,
        topic_ids=topic_ids,
        warnings=warnings,
    )
    return cfg


# ---------------------------------------------------------------------------
# Topic routing helpers
# ---------------------------------------------------------------------------

_TASK_STATUS_MAP: dict[str, BoardTopic] = {
    "idea":          BoardTopic.task_ideas,
    "refined":       BoardTopic.task_ideas,
    "ready":         BoardTopic.task_ready,
    "ready_for_dev": BoardTopic.task_ready,
    "in_progress":   BoardTopic.task_active,
    "review":        BoardTopic.task_active,
    "done":          BoardTopic.task_active,
    "blocked":       BoardTopic.task_blocked,
    "cancelled":     BoardTopic.task_active,
}

_BUG_STATUS_MAP: dict[str, BoardTopic] = {
    "new":         BoardTopic.bugs_new,
    "in_progress": BoardTopic.bugs_active,
    "verify":      BoardTopic.bugs_active,
    "closed":      BoardTopic.bugs_active,
    "need_info":   BoardTopic.needs_input,
    "cancelled":   BoardTopic.bugs_active,
}

_RELEASE_STATUS_MAP: dict[str, BoardTopic] = {
    "preparing":  BoardTopic.releases,
    "publishing": BoardTopic.releases,
    "published":  BoardTopic.releases,
    "failed":     BoardTopic.releases,
    "rollback":   BoardTopic.releases,
}

_TASK_STATUS_LABELS: dict[str, str] = {
    "idea":          "Идея",
    "refined":       "Детализирована",
    "ready":         "Готова к работе",
    "ready_for_dev": "Готова к разработке",
    "in_progress":   "В работе",
    "review":        "На ревью",
    "done":          "Готово",
    "blocked":       "Заблокирована",
    "cancelled":     "Отменена",
}

_BUG_STATUS_LABELS: dict[str, str] = {
    "new":         "Новый",
    "in_progress": "В работе",
    "verify":      "На проверке",
    "closed":      "Закрыт",
    "need_info":   "Нужна информация",
    "cancelled":   "Отменён",
}

_RELEASE_STATUS_LABELS: dict[str, str] = {
    "preparing":  "Готовится",
    "publishing": "Публикуется",
    "published":  "Опубликован",
    "failed":     "Ошибка",
    "rollback":   "Откат",
}


def topic_for_task_status(status: str) -> Optional[BoardTopic]:
    """Return the board topic for a given task status, or None if unknown."""
    return _TASK_STATUS_MAP.get(status)


def topic_for_bug_status(status: str) -> Optional[BoardTopic]:
    """Return the board topic for a given bug status, or None if unknown."""
    return _BUG_STATUS_MAP.get(status)


def topic_for_release_status(status: str) -> Optional[BoardTopic]:
    """Return the board topic for a given release status, or None if unknown."""
    return _RELEASE_STATUS_MAP.get(status)


def topic_key_for_task(task: dict) -> str:
    """
    Return the board topic *key* (string) for a task based on its status and fields.

    Maps orchestrator-native statuses (idea, refined, ready_for_dev, in_progress,
    review, done) and legacy statuses (ready, blocked, cancelled) to board topic keys.
    Falls back to "task_ideas" for any unknown status.

    A task with blocked_reason set is routed to "task_blocked" regardless of status,
    unless the status already maps to a more specific topic.
    """
    status = (task.get("status") or "").strip()
    topic = _TASK_STATUS_MAP.get(status)
    if topic is not None:
        return topic.value

    # Unknown status: route blocked tasks with a reason to task_blocked
    if task.get("blocked_reason"):
        return BoardTopic.task_blocked.value

    # Fallback
    return BoardTopic.task_ideas.value


def topic_for_decision() -> BoardTopic:
    return BoardTopic.decisions


def topic_for_agent_log() -> BoardTopic:
    return BoardTopic.agent_log


# ---------------------------------------------------------------------------
# Card formatters — pure functions, no I/O, no secrets, no absolute paths
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = 300) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def _priority_label(priority: str | None) -> str:
    labels = {
        "high":     "Высокий",
        "medium":   "Средний",
        "low":      "Низкий",
        "critical": "Критический",
    }
    return labels.get((priority or "").lower(), str(priority or "").capitalize()) if priority else ""


def format_task_board_card(task: dict) -> str:
    """
    Format a task dict as a human-readable board card (Russian).
    No JSON, no technical fields, no absolute paths.
    """
    status_raw = task.get("status", "")
    status_label = _TASK_STATUS_LABELS.get(status_raw, status_raw)

    icons = {
        "idea":        "💡",
        "ready":       "✅",
        "in_progress": "🚧",
        "review":      "🔍",
        "done":        "✔️",
        "blocked":     "⛔",
        "cancelled":   "❌",
    }
    icon = icons.get(status_raw, "🧩")

    item_id = task.get("id", "")
    title = task.get("title", "")
    priority = task.get("priority", "")
    description = (task.get("description") or "").strip()

    header = f"{icon} {item_id} — {title}" if item_id else f"{icon} {title}"
    lines = [header]
    lines.append(f"Статус: {status_label}")
    if priority:
        lines.append(f"Приоритет: {_priority_label(priority)}")

    depends_on = task.get("depends_on") or []
    if depends_on:
        lines.append(f"Зависит от: {', '.join(depends_on)}")

    blocked_reason = (task.get("blocked_reason") or "").strip()
    if blocked_reason and status_raw == "blocked":
        lines.append(f"Причина: {_truncate(blocked_reason, 150)}")

    if description:
        lines.append("")
        lines.append("Что нужно сделать:")
        lines.append(_truncate(description))

    return "\n".join(lines)


def format_bug_board_card(bug: dict) -> str:
    """
    Format a bug dict as a human-readable board card (Russian).
    """
    status_raw = bug.get("status", "")
    status_label = _BUG_STATUS_LABELS.get(status_raw, status_raw)

    item_id = bug.get("id", "")
    title = bug.get("title", "")
    severity = (bug.get("severity") or "").strip()
    description = (bug.get("description") or "").strip()

    sev_labels = {
        "critical": "Критическая",
        "high":     "Высокая",
        "medium":   "Средняя",
        "low":      "Низкая",
    }

    lines = [f"🐞 Баг: {title}"]
    if item_id:
        lines.append(f"ID: {item_id}")
    lines.append(f"Статус: {status_label}")
    if severity and severity.lower() not in {"unknown", ""}:
        lines.append(f"Серьёзность: {sev_labels.get(severity.lower(), severity)}")

    if description:
        lines.append("")
        lines.append("Описание:")
        lines.append(_truncate(description))

    return "\n".join(lines)


def format_release_board_card(release: dict) -> str:
    """
    Format a release dict as a human-readable board card (Russian).
    """
    status_raw = release.get("status", "")
    status_label = _RELEASE_STATUS_LABELS.get(status_raw, status_raw)

    release_id = release.get("id", "")
    version = (release.get("version") or release.get("name") or "").strip()
    task_ids = release.get("task_ids") or []
    notes = (release.get("notes") or "").strip()

    lines = ["🚀 Релиз"]
    if version:
        lines[0] = f"🚀 Релиз: {version}"
    if release_id:
        lines.append(f"ID: {release_id}")
    lines.append(f"Статус: {status_label}")
    if task_ids:
        lines.append(f"Задач: {len(task_ids)}")
    if notes:
        lines.append("")
        lines.append(_truncate(notes))

    return "\n".join(lines)


def format_decision_board_card(decision: dict) -> str:
    """
    Format an ADR/decision dict as a human-readable board card (Russian).
    """
    decision_id = decision.get("id", "")
    title = decision.get("title", "")
    status = (decision.get("status") or "").strip()
    date = (decision.get("date") or "").strip()

    status_labels = {
        "accepted":    "Принято",
        "proposed":    "На рассмотрении",
        "deprecated":  "Устарело",
        "superseded":  "Заменено",
        "rejected":    "Отклонено",
    }
    status_label = status_labels.get(status.lower(), status) if status else ""

    lines = [f"📌 Решение: {title}"]
    if decision_id:
        lines.append(f"ID: {decision_id}")
    if status_label:
        lines.append(f"Статус: {status_label}")
    if date:
        lines.append(f"Дата: {date}")

    return "\n".join(lines)


def format_task_tombstone(task: dict, new_topic_key: str) -> str:
    """
    Short Russian tombstone message left in the old topic when a task card is moved.

    Example:
        ➡️ TASK-12 перенесена

        Актуальная карточка теперь находится в другом топике.
        Новый статус: В работе

    ``new_topic_key`` is kept as a parameter for future use (e.g. linking to the new topic)
    but the current format shows the task's human-readable status label instead of the
    raw topic key so the tombstone stays readable without Telegram context.
    """
    task_id = task.get("id", "")
    status_raw = task.get("status", "")
    status_label = _TASK_STATUS_LABELS.get(status_raw, status_raw)

    lines = [f"➡️ {task_id} перенесена", ""]
    lines.append("Актуальная карточка теперь находится в другом топике.")
    if status_label:
        lines.append(f"Новый статус: {status_label}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inline keyboard helpers for task board cards
# ---------------------------------------------------------------------------

def parse_board_task_callback(data: str) -> "dict | None":
    """
    Parse a board task callback data string.

    Expected format:  ``board:task:{action}:{task_id}``
    Known actions:    ``focus``, ``start``

    Returns ``{"action": str, "task_id": str}`` or ``None`` if format is invalid.
    """
    if not data or not data.startswith("board:task:"):
        return None
    parts = data.split(":", 3)
    if len(parts) != 4:
        return None
    _, _, action, task_id = parts
    if not action or not task_id:
        return None
    return {"action": action, "task_id": task_id}


def build_task_card_keyboard(task: dict) -> "list[tuple[str, str]]":
    """
    Return inline button specs for a task board card.

    Returns a list of ``(label, callback_data)`` tuples.  Buttons by status:

    * ``ready_for_dev``:  ``[("🚧 В работу", "board:task:start:{id}"),
                             ("🎯 В фокус",   "board:task:focus:{id}")]``
    * all other statuses: ``[("🎯 В фокус",   "board:task:focus:{id}")]``

    Returns an empty list when the task has no ``id``.
    """
    task_id = task.get("id", "")
    if not task_id:
        return []
    status = (task.get("status") or "").strip()
    buttons: list[tuple[str, str]] = []
    if status == "ready_for_dev":
        buttons.append(("🚧 В работу", f"board:task:start:{task_id}"))
    buttons.append(("🎯 В фокус", f"board:task:focus:{task_id}"))
    return buttons


def make_inline_keyboard(buttons: "list[tuple[str, str]]") -> "Optional[object]":
    """
    Build an ``InlineKeyboardMarkup`` from ``(label, callback_data)`` pairs.

    All buttons are placed in a single row.
    Returns ``None`` when *buttons* is empty or the ``telegram`` package is unavailable.
    """
    if not buttons:
        return None
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except ImportError:
        return None
    row = [InlineKeyboardButton(label, callback_data=data) for label, data in buttons]
    return InlineKeyboardMarkup([row])


def format_agent_log_card(event: dict) -> str:
    """
    Format an agent event as a human-readable log card (Russian).
    """
    event_type = (event.get("type") or event.get("event") or "событие").strip()
    task_id = (event.get("task_id") or "").strip()
    message = (event.get("message") or event.get("summary") or "").strip()
    status = (event.get("status") or event.get("new_status") or "").strip()
    timestamp = (event.get("timestamp") or event.get("created_at") or "").strip()

    type_labels = {
        "run_next":      "▶️ Шаг выполнен",
        "advance":       "⏩ Задача продвинута",
        "error":         "❌ Ошибка",
        "status_change": "🔄 Смена статуса",
        "created":       "🆕 Создано",
        "note":          "📝 Заметка",
    }
    label = type_labels.get(event_type, f"📋 {event_type}")

    lines = [label]
    if task_id:
        lines.append(f"Задача: {task_id}")
    if status:
        lines.append(f"Статус: {status}")
    if message:
        lines.append(_truncate(message, 200))
    if timestamp:
        lines.append(f"Время: {timestamp[:19]}")  # trim microseconds if present

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Functional API — simple env-backed helpers
# ---------------------------------------------------------------------------

# Canonical ordered topic list: (key, human_name, env_var)
# Single source of truth — used by board-config, board-ping, and send_board_message.
BOARD_TOPICS: list[tuple[str, str, str]] = [
    ("task_ideas",   "Task Ideas",   "TELEGRAM_TOPIC_TASK_IDEAS"),
    ("task_ready",   "Task Ready",   "TELEGRAM_TOPIC_TASK_READY"),
    ("task_active",  "Task Active",  "TELEGRAM_TOPIC_TASK_ACTIVE"),
    ("task_blocked", "Task Blocked", "TELEGRAM_TOPIC_TASK_BLOCKED"),
    ("bugs_new",     "Bugs New",     "TELEGRAM_TOPIC_BUGS_NEW"),
    ("bugs_active",  "Bugs Active",  "TELEGRAM_TOPIC_BUGS_ACTIVE"),
    ("needs_input",  "Needs Input",  "TELEGRAM_TOPIC_NEEDS_INPUT"),
    ("releases",     "Releases",     "TELEGRAM_TOPIC_RELEASES"),
    ("agent_log",    "Agent Log",    "TELEGRAM_TOPIC_AGENT_LOG"),
    ("decisions",    "Decisions",    "TELEGRAM_TOPIC_DECISIONS"),
]

# String key → env var name — derived from BOARD_TOPICS, kept for convenience
_TOPIC_KEY_ENV: dict[str, str] = {key: env for key, _, env in BOARD_TOPICS}

# Russian display labels (used in format_board_config_status)
_TOPIC_LABELS: dict[str, str] = {
    "task_ideas":   "💡 Идеи задач",
    "task_ready":   "✅ Готовые задачи",
    "task_active":  "🚧 Активные задачи",
    "task_blocked": "⛔ Заблокированные",
    "bugs_new":     "🐞 Новые баги",
    "bugs_active":  "🛠 Активные баги",
    "needs_input":  "🟡 Нужна информация",
    "releases":     "🚀 Релизы",
    "agent_log":    "🧾 Лог агентов",
    "decisions":    "📌 Решения (ADR)",
}


def is_board_enabled() -> bool:
    """Return True when TELEGRAM_BOARD_ENABLED is a truthy value."""
    return _parse_bool(os.getenv("TELEGRAM_BOARD_ENABLED"), default=False)


def is_archive_on_move_enabled() -> bool:
    """Return True when old card should be replaced with a tombstone on topic move.

    Controlled by TELEGRAM_BOARD_ARCHIVE_OLD_ON_MOVE (default: true).
    Set to false to silently create a new card without editing the old one.
    Empty string is treated the same as unset (returns the default True).
    """
    raw = (os.getenv("TELEGRAM_BOARD_ARCHIVE_OLD_ON_MOVE") or "").strip()
    if not raw:
        return True  # default: archive on move
    return _parse_bool(raw, default=True)


def get_board_chat_id() -> Optional[str]:
    """Return TELEGRAM_BOARD_CHAT_ID, or None if not set."""
    val = (os.getenv("TELEGRAM_BOARD_CHAT_ID") or "").strip()
    return val if val else None


def get_topic_id(topic_key: str) -> Optional[int]:
    """
    Return the integer message_thread_id for a topic key, or None.

    Returns None when:
    - topic_key is unknown
    - env var is missing or empty
    - env var is not a valid integer
    """
    env_name = _TOPIC_KEY_ENV.get(topic_key)
    if not env_name:
        return None
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logging.warning("telegram_board: %s=%r is not a valid integer — ignored", env_name, raw)
        return None


def get_board_config_status() -> dict:
    """
    Return a dict describing the current board configuration state.

    Structure:
    {
        "enabled": bool,
        "chat_id_set": bool,
        "topics": {
            "task_ideas": {"env": "TELEGRAM_TOPIC_TASK_IDEAS", "set": bool, "value": int | None},
            ...
        }
    }
    """
    topics: dict[str, dict] = {}
    for key, env_name in _TOPIC_KEY_ENV.items():
        tid = get_topic_id(key)
        topics[key] = {
            "env": env_name,
            "set": tid is not None,
            "value": tid,
        }
    return {
        "enabled": is_board_enabled(),
        "chat_id_set": get_board_chat_id() is not None,
        "topics": topics,
    }


def format_board_config_status() -> str:
    """
    Human-readable Russian summary of the board configuration.
    Does not include token values or absolute paths.
    """
    status = get_board_config_status()
    enabled_label = "✅ включён" if status["enabled"] else "❌ выключен"
    chat_label = "✅ задан" if status["chat_id_set"] else "❌ не задан"

    lines = [
        "📋 Конфигурация Telegram Board",
        "",
        f"Board: {enabled_label}",
        f"Chat ID: {chat_label}",
        "",
        "Топики:",
    ]
    for key, info in status["topics"].items():
        label = _TOPIC_LABELS.get(key, key)
        state = "✅" if info["set"] else "—"
        lines.append(f"  {state} {label}")

    if not status["enabled"]:
        lines.append("")
        lines.append("Чтобы включить: TELEGRAM_BOARD_ENABLED=true в .env")

    return "\n".join(lines)


async def send_board_message(
    context: object,
    topic_key: str,
    text: str,
    reply_markup: object = None,
) -> Optional[int]:
    """
    Send a message to a board forum topic.

    Returns the message_id if sent, or None on any failure.
    Never raises — all exceptions are caught and logged.
    """
    if not is_board_enabled():
        return None

    board_chat_id = get_board_chat_id()
    if not board_chat_id:
        logging.warning("telegram_board: send_board_message called but TELEGRAM_BOARD_CHAT_ID not set")
        return None

    topic_id = get_topic_id(topic_key)
    if topic_id is None:
        logging.warning(
            "telegram_board: send_board_message topic_key=%r not configured, skipping", topic_key
        )
        return None

    try:
        kwargs: dict = {
            "chat_id": board_chat_id,
            "message_thread_id": topic_id,
            "text": text,
        }
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        msg = await context.bot.send_message(**kwargs)  # type: ignore[union-attr]
        return getattr(msg, "message_id", None)
    except Exception:
        logging.exception("telegram_board: failed to send message to topic %r", topic_key)
        return None


# ---------------------------------------------------------------------------
# Board ping — smoke-test helper
# ---------------------------------------------------------------------------

def get_send_timeout() -> float:
    """Return TELEGRAM_BOARD_SEND_TIMEOUT_SECONDS (default 20)."""
    raw = (os.getenv("TELEGRAM_BOARD_SEND_TIMEOUT_SECONDS") or "").strip()
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            logging.warning(
                "telegram_board: TELEGRAM_BOARD_SEND_TIMEOUT_SECONDS=%r is not a valid number — using default 20",
                raw,
            )
    return 20.0


def _is_message_not_modified_exception(exc: BaseException) -> bool:
    """Return True when exc is Telegram's 'Message is not modified' error."""
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "message is not modified",
        "specified new message content and reply markup are exactly the same",
    ))


def _is_timeout_exception(exc: BaseException) -> bool:
    """Return True when exc looks like a network / Telegram API timeout."""
    type_name = type(exc).__name__.lower()
    msg = str(exc).lower()
    # Common timeout class names from python-telegram-bot, aiohttp, asyncio, httpx, requests
    timeout_names = {
        "timedout", "timeout", "timeouterror", "asynciotimeouterror",
        "readtimeout", "connecttimeout", "writetimeout",
    }
    if type_name in timeout_names:
        return True
    for part in type_name.split("."):
        if part in timeout_names:
            return True
    # Fall back to message heuristics
    return any(kw in msg for kw in ("timed out", "timeout", "time out"))


async def ping_board_topics(
    bot: object,
    topic_filter: Optional[str] = None,
) -> list[dict]:
    """
    Send a ping message to every configured board topic (or a single one).

    Args:
        bot:          Telegram Bot instance.
        topic_filter: If given, only ping this topic key (e.g. "agent_log").
                      If None, ping all topics in BOARD_TOPICS order.

    Returns a list of result dicts:
    {
        "key":    str,                              # topic key e.g. "task_ideas"
        "name":   str,                              # human name e.g. "Task Ideas"
        "env":    str,                              # env var name
        "status": "ok" | "missing" | "error" | "timeout",
        "error":  str | None,
    }

    Does not raise. Never prints tokens or absolute paths.
    Timeout errors get status "timeout" with a warning message.
    Network / Telegram hard errors get status "error".
    """
    if not is_board_enabled():
        raise ValueError("Telegram Board is disabled (TELEGRAM_BOARD_ENABLED=false)")

    board_chat_id = get_board_chat_id()
    if not board_chat_id:
        raise ValueError("TELEGRAM_BOARD_CHAT_ID is not set")

    timeout_sec = get_send_timeout()

    # Build the topic list to ping
    if topic_filter is not None:
        topics_to_ping = [(k, n, e) for k, n, e in BOARD_TOPICS if k == topic_filter]
    else:
        topics_to_ping = list(BOARD_TOPICS)

    results: list[dict] = []
    for key, name, env_name in topics_to_ping:
        topic_id = get_topic_id(key)
        if topic_id is None:
            results.append({
                "key": key, "name": name, "env": env_name,
                "status": "missing", "error": f"{env_name} not set",
            })
            continue
        try:
            await bot.send_message(  # type: ignore[union-attr]
                chat_id=board_chat_id,
                message_thread_id=topic_id,
                text=f"✅ ping: {name}",
                read_timeout=timeout_sec,
                write_timeout=timeout_sec,
                connect_timeout=timeout_sec,
            )
            results.append({
                "key": key, "name": name, "env": env_name,
                "status": "ok", "error": None,
            })
        except Exception as exc:
            short_err = str(exc)[:120]
            if _is_timeout_exception(exc):
                logging.warning(
                    "telegram_board: ping timeout for %r (%.0fs): %s", key, timeout_sec, short_err
                )
                results.append({
                    "key": key, "name": name, "env": env_name,
                    "status": "timeout", "error": short_err,
                })
            else:
                logging.warning("telegram_board: ping failed for %r: %s", key, short_err)
                results.append({
                    "key": key, "name": name, "env": env_name,
                    "status": "error", "error": short_err,
                })

    return results


async def upsert_task_board_card(
    bot: object,
    task: dict,
    *,
    force_new: bool = False,
) -> dict:
    """Create or update a task card on the Telegram Board (topic-aware upsert).

    Returns a result dict:
    {
        "status":     "created" | "updated" | "recreated" | "moved" |
                      "moved_archive_failed" | "timeout" | "error" | "skipped",
        "task_id":    str,
        "topic_key":  str,           # desired topic key
        "message_id": int | None,
        "reason":     str | None,
        "prev_topic_key": str | None,  # set when status is "moved"/"moved_archive_failed"
    }

    Statuses:
    - created              — new message sent, mapping saved
    - updated              — existing message edited in-place, mapping updated
    - recreated            — existing message was deleted; new message sent, mapping updated
    - moved                — task moved to a new topic; new card created, old card archived (tombstone)
    - moved_archive_failed — task moved, new card created, but tombstone edit on old card failed
    - timeout              — Telegram did not respond in time (warning, not a hard crash)
    - error                — hard Telegram/network failure
    - skipped              — board disabled / chat id missing / topic not configured

    Does not raise.  Never prints token or absolute paths.
    Topic-move archiving is controlled by TELEGRAM_BOARD_ARCHIVE_OLD_ON_MOVE (default: true).
    """
    import telegram_message_links

    task_id: str = task.get("id", "")
    topic_key: str = topic_key_for_task(task)
    card_text: str = format_task_board_card(task)
    card_keyboard: object = make_inline_keyboard(build_task_card_keyboard(task))

    def _result(
        status: str,
        message_id: "Optional[int]" = None,
        reason: "Optional[str]" = None,
        prev_topic_key: "Optional[str]" = None,
    ) -> dict:
        return {
            "status": status,
            "task_id": task_id,
            "topic_key": topic_key,
            "message_id": message_id,
            "reason": reason,
            "prev_topic_key": prev_topic_key,
        }

    if not is_board_enabled():
        return _result("skipped", reason="Board disabled (TELEGRAM_BOARD_ENABLED=false)")

    board_chat_id = get_board_chat_id()
    if not board_chat_id:
        return _result("skipped", reason="TELEGRAM_BOARD_CHAT_ID not set")

    topic_id = get_topic_id(topic_key)
    if topic_id is None:
        env_var = _TOPIC_KEY_ENV.get(topic_key, "")
        return _result("skipped", reason=f"Topic {topic_key!r} not configured ({env_var} not set)")

    timeout_sec = get_send_timeout()

    # --- Look up existing Board card ---
    existing_link = None if force_new else telegram_message_links.find_board_link("task", task_id)

    # --- MOVE path: topic changed ---
    if existing_link and existing_link.get("topic_key") != topic_key:
        prev_topic_key: str = existing_link["topic_key"]
        old_message_id: int = existing_link["telegram_message_id"]
        old_thread_id: "Optional[int]" = existing_link.get("message_thread_id")

        logging.info(
            "telegram_board: task %r topic changed %r -> %r, moving card",
            task_id, prev_topic_key, topic_key,
        )

        # 1. Send new card in the new topic (must succeed; if it fails, abort)
        try:
            _send_kwargs: dict = {
                "chat_id": board_chat_id,
                "message_thread_id": topic_id,
                "text": card_text,
                "read_timeout": timeout_sec,
                "write_timeout": timeout_sec,
                "connect_timeout": timeout_sec,
            }
            if card_keyboard is not None:
                _send_kwargs["reply_markup"] = card_keyboard
            new_msg = await bot.send_message(**_send_kwargs)  # type: ignore[union-attr]
        except Exception as send_exc:
            short = str(send_exc)[:200]
            if _is_timeout_exception(send_exc):
                logging.warning("telegram_board: send timeout during move for task %r: %s", task_id, short)
                return _result("timeout", reason=short, prev_topic_key=prev_topic_key)
            logging.warning("telegram_board: send failed during move for task %r: %s", task_id, short)
            return _result("error", reason=short, prev_topic_key=prev_topic_key)

        new_message_id = getattr(new_msg, "message_id", None)

        # 2. Update mapping to new topic/message
        if new_message_id is not None:
            telegram_message_links.upsert_board_link(
                chat_id=board_chat_id,
                message_id=new_message_id,
                work_item_type="task",
                work_item_id=task_id,
                message_thread_id=topic_id,
                topic_key=topic_key,
            )

        # 3. Archive old card with tombstone (best-effort)
        if is_archive_on_move_enabled():
            tombstone = format_task_tombstone(task, topic_key)
            try:
                await bot.edit_message_text(  # type: ignore[union-attr]
                    chat_id=board_chat_id,
                    message_id=old_message_id,
                    text=tombstone,
                    read_timeout=timeout_sec,
                    write_timeout=timeout_sec,
                    connect_timeout=timeout_sec,
                )
                return _result("moved", message_id=new_message_id, prev_topic_key=prev_topic_key)
            except Exception as tomb_exc:
                short = str(tomb_exc)[:200]
                logging.warning(
                    "telegram_board: tombstone edit failed for old message %s of task %r: %s",
                    old_message_id, task_id, short,
                )
                return _result(
                    "moved_archive_failed",
                    message_id=new_message_id,
                    reason=short,
                    prev_topic_key=prev_topic_key,
                )
        else:
            # Archive disabled — just move silently
            return _result("moved", message_id=new_message_id, prev_topic_key=prev_topic_key)

    # --- SAME-TOPIC path: edit existing card ---
    if existing_link:
        existing_message_id: int = existing_link["telegram_message_id"]
        try:
            _edit_kwargs: dict = {
                "chat_id": board_chat_id,
                "message_id": existing_message_id,
                "text": card_text,
                "read_timeout": timeout_sec,
                "write_timeout": timeout_sec,
                "connect_timeout": timeout_sec,
            }
            if card_keyboard is not None:
                _edit_kwargs["reply_markup"] = card_keyboard
            await bot.edit_message_text(**_edit_kwargs)  # type: ignore[union-attr]
            telegram_message_links.upsert_board_link(
                chat_id=board_chat_id,
                message_id=existing_message_id,
                work_item_type="task",
                work_item_id=task_id,
                message_thread_id=existing_link.get("message_thread_id"),
                topic_key=topic_key,
            )
            return _result("updated", message_id=existing_message_id)
        except Exception as edit_exc:
            short = str(edit_exc)[:200]
            if _is_timeout_exception(edit_exc):
                logging.warning("telegram_board: edit timeout for task %r: %s", task_id, short)
                return _result("timeout", reason=short)
            if _is_message_not_modified_exception(edit_exc):
                logging.info("telegram_board: card already up to date for task %r", task_id)
                return _result(
                    "unchanged",
                    message_id=existing_message_id,
                    reason="card is already up to date",
                )
            # Message deleted / inaccessible — fall through to recreate
            low = short.lower()
            if any(kw in low for kw in (
                "message to edit not found",
                "message_id_invalid",
                "message can't be edited",
                "message not found",
                "bad request",
            )):
                logging.warning(
                    "telegram_board: existing message %s gone, will recreate for task %r",
                    existing_message_id, task_id,
                )
                existing_link = None  # signal to recreate below
            else:
                logging.warning("telegram_board: edit failed for task %r: %s", task_id, short)
                return _result("error", reason=short)

    # --- CREATE / RECREATE path ---
    action = (
        "recreated"
        if (existing_link is None and not force_new
            and telegram_message_links.find_board_link("task", task_id))
        else "created"
    )
    try:
        _create_kwargs: dict = {
            "chat_id": board_chat_id,
            "message_thread_id": topic_id,
            "text": card_text,
            "read_timeout": timeout_sec,
            "write_timeout": timeout_sec,
            "connect_timeout": timeout_sec,
        }
        if card_keyboard is not None:
            _create_kwargs["reply_markup"] = card_keyboard
        msg = await bot.send_message(**_create_kwargs)  # type: ignore[union-attr]
    except Exception as send_exc:
        short = str(send_exc)[:200]
        if _is_timeout_exception(send_exc):
            logging.warning("telegram_board: send timeout for task %r: %s", task_id, short)
            return _result("timeout", reason=short)
        logging.warning("telegram_board: send failed for task %r: %s", task_id, short)
        return _result("error", reason=short)

    new_message_id = getattr(msg, "message_id", None)
    if new_message_id is not None:
        telegram_message_links.upsert_board_link(
            chat_id=board_chat_id,
            message_id=new_message_id,
            work_item_type="task",
            work_item_id=task_id,
            message_thread_id=topic_id,
            topic_key=topic_key,
        )

    return _result(action, message_id=new_message_id)


def format_ping_results(results: list[dict]) -> str:
    """Format ping results as human-readable text for Telegram or terminal."""
    lines = ["Telegram Board ping result:"]
    for r in results:
        if r["status"] == "ok":
            lines.append(f"✅ {r['name']}")
        elif r["status"] == "missing":
            lines.append(f"— {r['name']} (not configured)")
        elif r["status"] == "timeout":
            lines.append(
                f"⚠️ {r['name']} — timeout: сообщение могло быть отправлено, проверь топик"
            )
        else:
            lines.append(f"❌ {r['name']} — {r['error']}")
    return "\n".join(lines)
