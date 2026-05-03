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
    "idea":        BoardTopic.task_ideas,
    "ready":       BoardTopic.task_ready,
    "in_progress": BoardTopic.task_active,
    "review":      BoardTopic.task_active,
    "done":        BoardTopic.task_active,
    "blocked":     BoardTopic.task_blocked,
    "cancelled":   BoardTopic.task_active,
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
    "idea":        "Идея",
    "ready":       "Готова к работе",
    "in_progress": "В работе",
    "review":      "На ревью",
    "done":        "Готово",
    "blocked":     "Заблокирована",
    "cancelled":   "Отменена",
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

    lines = [f"{icon} Задача: {title}"]
    if item_id:
        lines.append(f"ID: {item_id}")
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
