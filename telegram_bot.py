from __future__ import annotations

import datetime
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import backlog
from conversation_context import clear_focus, get_focus, set_active_decision, set_active_release, set_active_task
from managed_project import validate_managed_repo_path
import orchestrator
from project_manager import get_project_status
from release_manager import load_releases
from speech_to_text import (
    SpeechToTextError,
    cleanup_voice_files,
    convert_voice_to_wav,
    ensure_voice_work_dir,
    is_voice_enabled,
    should_keep_voice_files,
    transcribe_audio,
)
from supervisor import (
    READ_ONLY_ACTIONS,
    RISKY_ACTIONS,
    SUPPORTED_ACTIONS,
    SupervisorError,
    execute_supervisor_action,
    plan_supervisor_action,
)
import telegram_message_links
import telegram_fast_router
import telegram_create_router


def truncate_text(text: str, limit: int = 3500) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: limit - 15] + "\n...[truncated]"


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_telegram_config() -> dict:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    owner_id = (os.getenv("TELEGRAM_OWNER_ID") or "").strip()
    dry_run_default = _parse_bool(os.getenv("TELEGRAM_DRY_RUN_BY_DEFAULT"), default=True)

    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required to run Telegram bot.")
    if not owner_id:
        raise ValueError("TELEGRAM_OWNER_ID is required to run Telegram bot.")

    return {
        "token": token,
        "owner_id": owner_id,
        "dry_run_by_default": dry_run_default,
    }


PENDING_ACTIONS: dict[str, dict] = {}

# Focus-related supervisor action names — don't append the focus indicator to their replies
# (the reply IS about focus, appending would be redundant).
_FOCUS_ACTIONS: frozenset[str] = frozenset({
    "focus", "clear_focus", "set_focus_task", "set_focus_release", "set_focus_decision",
})

# Regex to detect a TASK-X / BUG-X id in free text (case-insensitive).
_TASK_ID_RE = re.compile(r"\b((?:TASK|BUG)-\d+)\b", re.IGNORECASE)

# Keywords/phrases that signal "switch focus to this task".
_FOCUS_SWITCH_KEYWORDS = (
    "в фокус",
    "переключись на",
    "возьми",
    "фокус на",
    "сфокусируйся",
    "перейди к",
    "возьми в фокус",
)

# Russian status labels used in focus display
_FOCUS_STATUS_LABELS: dict[str, str] = {
    "idea": "Идея",
    "refined": "Детализирована",
    "ready": "Готова к работе",
    "ready_for_dev": "Готова к разработке",
    "in_progress": "В работе",
    "review": "На ревью",
    "done": "Готово",
    "blocked": "Заблокирована",
    "cancelled": "Отменена",
}


def get_status_chat_id() -> str | None:
    v = (os.getenv("TELEGRAM_STATUS_CHAT_ID") or "").strip()
    return v if v else None


def is_status_notifications_enabled() -> bool:
    return _parse_bool(os.getenv("TELEGRAM_NOTIFY_AGENT_EVENTS"), default=True)


async def send_status_notification(context: Any, text: str) -> None:
    chat_id = get_status_chat_id()
    if not chat_id or not is_status_notifications_enabled():
        return
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        logging.exception("Failed to send status notification")


def is_debug_mode() -> bool:
    return _parse_bool(os.getenv("TELEGRAM_DEBUG_MODE"), default=False)


# ---------------------------------------------------------------------------
# Persistent error logging
# ---------------------------------------------------------------------------

_ERROR_LOG_DIR = Path(".tmp/logs/errors")


def write_error_log(
    exc: BaseException,
    update: Any = None,
    handler_name: str | None = None,
) -> "tuple[str, Path]":
    """Write an error to ``.tmp/logs/errors/TG-YYYYMMDD-HHMMSS-<4hex>.log``.

    Returns ``(error_id, log_path)``.  Never raises — if the write fails the
    error is logged to the standard logger and a fallback ID is returned.

    Security: TELEGRAM_BOT_TOKEN and other secrets are never written.
    Only the user_id, chat_id, and a truncated message text are included.
    """
    import traceback as _tb_mod

    now = datetime.datetime.now()
    short_id = uuid.uuid4().hex[:4]
    error_id = f"TG-{now.strftime('%Y%m%d-%H%M%S')}-{short_id}"

    # --- safe context from update (no secrets) ---
    update_type = type(update).__name__ if update is not None else "None"
    user_id_str = ""
    chat_id_str = ""
    msg_text = ""
    if update is not None:
        user = getattr(update, "effective_user", None)
        if user is not None:
            user_id_str = str(getattr(user, "id", ""))
        chat = getattr(update, "effective_chat", None)
        if chat is not None:
            chat_id_str = str(getattr(chat, "id", ""))
        message = getattr(update, "message", None)
        if message is not None:
            raw = getattr(message, "text", "") or ""
            msg_text = raw[:500]  # truncate; never log tokens

    tb_str = "".join(_tb_mod.format_exception(type(exc), exc, exc.__traceback__))

    content_lines = [
        f"timestamp: {now.isoformat()}",
        f"error_id: {error_id}",
        f"handler: {handler_name or 'unknown'}",
        f"update_type: {update_type}",
        f"user_id: {user_id_str or '(none)'}",
        f"chat_id: {chat_id_str or '(none)'}",
        f"message_text: {msg_text or '(none)'}",
        f"exception_class: {type(exc).__name__}",
        f"exception_message: {exc}",
        "",
        "--- traceback ---",
        tb_str,
    ]

    log_path = _ERROR_LOG_DIR / f"{error_id}.log"
    try:
        _ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(content_lines), encoding="utf-8")
    except Exception as write_exc:
        logging.exception("Failed to write error log %s: %s", error_id, write_exc)

    return error_id, log_path


def _is_empty_output_error(exc: BaseException) -> bool:
    """Return True if *exc* is the "Claude Code returned empty output" error."""
    return "Claude Code returned empty output" in str(exc)


# Human-readable labels for each supervisor action (icon, short Russian description)
_ACTION_ICONS: dict[str, tuple[str, str]] = {
    "create_task": ("🧩", "Создам задачу"),
    "create_bug": ("🐞", "Создам баг"),
    "run_next": ("▶️", "Выполню следующий шаг задачи"),
    "run_all": ("⚡", "Запущу все задачи в очереди"),
    "list_tasks": ("📋", "Покажу список задач"),
    "show_task": ("🔍", "Покажу задачу"),
    "backlog": ("📋", "Покажу backlog"),
    "ready": ("✅", "Покажу готовые задачи"),
    "blocked": ("🚫", "Покажу заблокированные задачи"),
    "next_task": ("➡️", "Найду следующую задачу"),
    "context": ("📌", "Покажу контекст"),
    "agents": ("🤖", "Покажу агентов"),
    "config": ("⚙️", "Покажу конфигурацию"),
    "create_release": ("🚀", "Создам релиз"),
    "list_releases": ("📦", "Покажу список релизов"),
    "show_release": ("📦", "Покажу релиз"),
    "release_readiness": ("📊", "Проверю готовность релиза"),
    "release_notes": ("📝", "Подготовлю release notes"),
    "release_risks": ("⚠️", "Покажу риски релиза"),
    "rollback_plan": ("🔄", "Покажу план отката"),
    "create_decision": ("📐", "Зафиксирую архитектурное решение"),
    "list_decisions": ("📐", "Покажу список решений"),
    "show_decision": ("📐", "Покажу решение"),
    "repo_scan": ("🔍", "Просканирую репозиторий"),
    "repo_tree": ("🌳", "Покажу дерево файлов"),
    "repo_search": ("🔍", "Поищу в репозитории"),
    "repo_file": ("📄", "Прочитаю файл"),
    "attach_repo_context": ("🔗", "Прикреплю контекст репозитория"),
    "dev_plan": ("🛠️", "Подготовлю план разработки"),
    "run_command": ("⚙️", "Запущу команду"),
    "apply_patch": ("🩹", "Применю патч"),
    "add_dependency": ("🔗", "Добавлю зависимость"),
    "remove_dependency": ("🔗", "Удалю зависимость"),
    "block_task": ("🚫", "Заблокирую задачу"),
    "unblock_task": ("✅", "Разблокирую задачу"),
}


def _format_plan_human(plan: dict) -> str:
    """User-facing Russian plan summary — no technical internals."""
    action = plan.get("action") or {}
    action_name = action.get("name", "")
    args = action.get("args") or {}
    explanation = str(plan.get("explanation", "")).strip()
    requires_confirmation = plan.get("requires_confirmation", False)
    warnings = plan.get("warnings") or []

    icon, label = _ACTION_ICONS.get(action_name, ("🤔", "Планирую действие"))
    lines = [f"{icon} {label}"]

    title = args.get("title", "")
    description = args.get("description", "")
    task_id = args.get("task_id") or args.get("id", "")

    if title:
        lines.append(f"Название: {title}")
    if description:
        lines.append(f"Описание: {truncate_text(description, limit=300)}")
    if task_id and action_name not in {"create_task", "create_bug"}:
        lines.append(f"Задача: {task_id}")

    if explanation and explanation.lower() not in {"ok", "—", "-"}:
        lines.append(f"\n{explanation}")

    if requires_confirmation:
        lines.append("\n⚠️ Рискованное действие — для выполнения используй /yes <запрос>")

    for w in warnings:
        lines.append(f"⚠️ {w}")

    return truncate_text("\n".join(lines))


def _result_items_human(data: Any, action: str = "") -> str:
    """Format action result data as readable Russian text."""
    if data is None:
        return ""
    if isinstance(data, str):
        return truncate_text(data, limit=2000)
    if isinstance(data, list):
        if not data:
            return "Список пуст."
        lines = []
        for item in data[:15]:
            if isinstance(item, dict):
                item_id = item.get("id", "")
                title = item.get("title", "")
                status = item.get("status", "")
                if item_id and title:
                    lines.append(f"• {item_id}: {title}" + (f" [{status}]" if status else ""))
            else:
                lines.append(f"• {item}")
        if len(data) > 15:
            lines.append(f"... и ещё {len(data) - 15}")
        return "\n".join(lines)
    if isinstance(data, dict):
        msg = data.get("message")
        if msg and isinstance(msg, str):
            return truncate_text(msg, limit=2000)
        for key in ("summary", "text", "title"):
            val = data.get(key)
            if val and isinstance(val, str):
                return truncate_text(val, limit=2000)
    return ""


def _format_result_human(result: dict) -> str:
    """User-facing Russian execution result — no technical internals."""
    action = result.get("action", "")
    executed = bool(result.get("executed"))
    r = result.get("result") or {}
    if not isinstance(r, dict):
        r_raw = result.get("result")
        r = {}
    else:
        r_raw = r

    if not executed:
        msg = result.get("message") or result.get("refusal_reason") or "Действие не выполнено."
        return f"ℹ️ {truncate_text(str(msg), limit=2000)}"

    if action == "create_task":
        task_id = r.get("id", "?")
        title = r.get("title", "?")
        status = r.get("status", "idea")
        return f"✅ Задача создана\n\n{task_id}: {title}\nСтатус: {status}"

    if action == "create_bug":
        task_id = r.get("id", "?")
        title = r.get("title", "?")
        status = r.get("status", "idea")
        severity = r.get("severity", "")
        lines = [f"✅ Баг создан", "", f"{task_id}: {title}", f"Статус: {status}"]
        if severity and severity != "unknown":
            lines.append(f"Серьёзность: {severity}")
        return "\n".join(lines)

    # Generic successful result: try to surface a readable message
    body = _result_items_human(r_raw, action)
    if body:
        return f"✅ Готово\n\n{body}"
    msg = result.get("message", "")
    if msg:
        return f"✅ Готово\n\n{truncate_text(str(msg), limit=2000)}"
    return "✅ Выполнено"


def is_owner(update: Any, owner_id: str) -> bool:
    user = getattr(update, "effective_user", None)
    if user is None:
        return False
    user_id = getattr(user, "id", None)
    return str(user_id) == str(owner_id)


def _format_plan_debug(plan: dict) -> str:
    action = plan.get("action") or {}
    args = action.get("args") or {}
    warnings = plan.get("warnings") or []

    lines = [
        "План действия:",
        f"- intent: {plan.get('intent')}",
        f"- confidence: {plan.get('confidence')}",
        f"- требует подтверждения: {'да' if plan.get('requires_confirmation') else 'нет'}",
        f"- action: {action.get('name')}",
        "",
        "Аргументы:",
        truncate_text(json.dumps(args, ensure_ascii=False, indent=2), limit=1200),
        "",
        "Пояснение:",
        truncate_text(str(plan.get("explanation", "")), limit=800),
    ]

    if warnings:
        lines.extend(["", "Предупреждения:"])
        for item in warnings:
            lines.append(f"- {item}")

    return truncate_text("\n".join(lines))


def format_supervisor_plan(plan: dict, debug: bool = False) -> str:
    if debug:
        return _format_plan_debug(plan)
    return _format_plan_human(plan)


def _result_summary(result: Any) -> str:
    if result is None:
        return "(empty)"
    if isinstance(result, str):
        return truncate_text(result, limit=900)
    if isinstance(result, list):
        return f"Список элементов: {len(result)}"
    if isinstance(result, dict):
        if "message" in result and isinstance(result["message"], str):
            return truncate_text(result["message"], limit=900)
        keys = ", ".join(sorted(result.keys())[:12])
        return f"Объект: keys=[{keys}]"
    return truncate_text(str(result), limit=900)


def _format_result_debug(result: dict) -> str:
    action = result.get("action")
    executed = bool(result.get("executed"))
    status = "успех" if executed else "не выполнено"

    lines = [
        "Результат выполнения:",
        f"- executed: {'true' if executed else 'false'}",
        f"- action: {action}",
        f"- статус: {status}",
    ]

    if "refusal_reason" in result:
        lines.extend(["", "Причина отказа:", truncate_text(str(result.get("refusal_reason")), limit=900)])
    elif "message" in result:
        lines.extend(["", "Сообщение:", truncate_text(str(result.get("message")), limit=900)])
    elif "result" in result:
        lines.extend(["", "Сводка:", _result_summary(result.get("result"))])

    if "error" in result:
        lines.extend(["", "Ошибка:", truncate_text(str(result.get("error")), limit=900)])

    return truncate_text("\n".join(lines))


def format_supervisor_execution_result(result: dict, debug: bool = False) -> str:
    if debug:
        return _format_result_debug(result)
    return _format_result_human(result)


def format_focus(focus: dict, task: "dict | None" = None) -> str:
    """Clean human-readable focus summary for /focus and focus-related commands.

    Args:
        focus: dict returned by ``get_focus()``.
        task:  optional pre-loaded task dict (avoids an extra DB call from callers
               that have already loaded the task).
    """
    task_id = focus.get("active_task_id")
    release_id = focus.get("active_release_id")
    decision_id = focus.get("active_decision_id")

    if task_id:
        if task is None:
            task = orchestrator.get_task(task_id)
        title = (task.get("title", "") if task else "")
        status_raw = (task.get("status", "") if task else "")
        status_label = _FOCUS_STATUS_LABELS.get(status_raw, status_raw)
        lines = [f"🎯 Фокус: {task_id}"]
        if title:
            lines.append(f"Название: {title}")
        if status_label:
            lines.append(f"Статус: {status_label}")
        lines.append("")
        lines.append("Пиши сюда о задаче. Снять: /clear_focus")
        return "\n".join(lines)

    if release_id:
        return f"🎯 Фокус: релиз {release_id}\n\nСнять: /clear_focus"

    if decision_id:
        return f"🎯 Фокус: решение {decision_id}\n\nСнять: /clear_focus"

    return "Фокус не выбран."


def format_focus_indicator(focus: dict, task: "dict | None" = None) -> str:
    """Short one-line focus indicator for appending to other responses.

    Returns an empty string when no focus is set.
    """
    task_id = focus.get("active_task_id")
    if task_id:
        if task is None:
            task = orchestrator.get_task(task_id)
        title = (task.get("title", "") if task else "")
        if title:
            return f"🎯 Фокус: {task_id} — {title}"
        return f"🎯 Фокус: {task_id}"
    release_id = focus.get("active_release_id")
    if release_id:
        return f"🎯 Фокус: {release_id}"
    decision_id = focus.get("active_decision_id")
    if decision_id:
        return f"🎯 Фокус: {decision_id}"
    return ""


def _check_focus_switch(text: str) -> "str | None":
    """Detect natural-language focus-switch requests and return the task/bug ID.

    Matches messages like:
      "возьми TASK-2 в фокус"
      "переключись на TASK-5"
      "фокус на BUG-3"

    Returns the uppercased ID string, or None if not a focus-switch request.
    """
    norm = text.lower().strip()
    has_indicator = any(kw in norm for kw in _FOCUS_SWITCH_KEYWORDS)
    if not has_indicator:
        return None
    m = _TASK_ID_RE.search(text)
    return m.group(1).upper() if m else None


def _telegram_session(update: Any) -> tuple[str, str, str]:
    user = getattr(update, "effective_user", None)
    user_id = str(getattr(user, "id", "")) if user is not None else ""
    return (f"telegram:{user_id or 'unknown'}", user_id, "telegram")


async def _reply(update: Any, text: str) -> None:
    message = getattr(update, "message", None)
    if message is None:
        return
    await message.reply_text(truncate_text(text))


def _extract_text(context: Any, update: Any) -> str:
    args = getattr(context, "args", None) or []
    if args:
        return " ".join(args).strip()
    message = getattr(update, "message", None)
    text = getattr(message, "text", "") if message else ""
    parts = str(text).split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    return ""


async def _run_dry(
    update: Any,
    user_text: str,
    session_id: str,
    user_id: str,
    channel: str,
    context: Any = None,
) -> None:
    if not user_text:
        await _reply(update, "Пустой запрос. Пример: /dryrun Создай задачу на healthcheck")
        return
    try:
        plan = plan_supervisor_action(user_text, session_id=session_id, user_id=user_id, channel=channel)
    except SupervisorError as exc:
        logging.exception("SupervisorError in _run_dry: %s", exc)
        await _reply(update, "Не смог разобрать ответ Supervisor. Попробуй переформулировать запрос или посмотри логи.")
        return
    if plan.get("intent") in {"clarify", "unknown"}:
        await _reply(update, plan.get("explanation", "Уточни запрос."))
        return

    if plan.get("action"):
        PENDING_ACTIONS[session_id] = {
            "text": user_text,
            "plan": plan,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "requires_confirmation": bool(plan.get("requires_confirmation", False)),
        }

    plan_text = format_supervisor_plan(plan, debug=is_debug_mode())

    # Append focus indicator when there is an active focus and the plan is not
    # a focus-management action itself (that would be redundant).
    action_name_dry = (plan.get("action") or {}).get("name", "")
    if action_name_dry not in _FOCUS_ACTIONS:
        _dry_focus = get_focus(session_id, user_id=user_id, channel=channel)
        _dry_indicator = format_focus_indicator(_dry_focus)
        if _dry_indicator:
            plan_text = plan_text + f"\n\n{_dry_indicator}"

    if plan.get("action"):
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Выполнить", callback_data="confirm_pending_action"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_pending_action"),
            ]])
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(truncate_text(plan_text), reply_markup=keyboard)
                return
        except ImportError:
            pass

    await _reply(update, plan_text)


def _format_task_card(item: dict, work_item_type: str) -> str:
    """Format a task/bug as a human-readable Telegram card message (Russian)."""
    if work_item_type == "task":
        header = "🧩 Новая задача"
    else:
        header = "🐞 Новый баг"

    item_id = item.get("id", "?")
    title = item.get("title", "?")
    status = item.get("status", "?")
    description = (item.get("description", "") or "").strip()
    severity = (item.get("severity", "") or "").strip()

    lines = [
        header,
        f"ID: {item_id}",
        f"Название: {title}",
    ]

    if description:
        label = "Серьёзность" if (work_item_type == "bug" and severity and severity != "unknown") else None
        if label:
            lines.append(f"\n{label}: {severity}")
        lines.append(f"\nЧто нужно сделать:\n{truncate_text(description, limit=400)}")
    elif work_item_type == "bug" and severity and severity != "unknown":
        lines.append(f"\nСерьёзность: {severity}")

    lines.append(f"\nСтатус: {status}")
    lines.append("\nОтветь на это сообщение, чтобы работать именно с этой задачей.")
    return "\n".join(lines)


async def _send_task_card(context: Any, item: dict, work_item_type: str) -> None:
    """Send a task/bug card to the status chat and store the message link."""
    chat_id = get_status_chat_id()
    if not chat_id or not is_status_notifications_enabled():
        return
    card_text = _format_task_card(item, work_item_type)
    try:
        sent = await context.bot.send_message(chat_id=chat_id, text=card_text)
        # Store Telegram message -> work item mapping so replies can be routed back.
        # This lookup is done locally (no LLM call) to keep token usage minimal.
        msg_id = getattr(sent, "message_id", None)
        if msg_id is not None:
            telegram_message_links.add_message_link(
                chat_id, msg_id, work_item_type, item.get("id", "?")
            )
    except Exception:
        logging.exception("Failed to send task card to status chat")


# Actions that mutate task state and should trigger an automatic Board card sync.
_BOARD_SYNC_ACTIONS: frozenset[str] = frozenset({
    "create_task",
    "create_bug",
    "run_next",
    "run_all",
    "advance_task_safely",
    "prepare_task_for_dev",
})


async def _board_sync_after_action(
    context: Any,
    action_name: str,
    action_args: dict,
    result: dict,
) -> None:
    """Sync affected task(s) to the Telegram Board after a state-changing action.

    Uses ``context.bot`` when available so no extra Bot instance is created.
    Never raises — all errors are logged at WARNING level.
    """
    if action_name not in _BOARD_SYNC_ACTIONS:
        return
    try:
        import telegram_board as _tb
    except ImportError:
        return

    bot = getattr(context, "bot", None) if context is not None else None
    raw = result.get("result")

    if action_name == "run_all":
        # run_all returns a list of {id, status, ...} items
        items = raw if isinstance(raw, list) else []
        for item in items:
            tid = item.get("id") if isinstance(item, dict) else None
            if tid:
                await _tb.sync_task_to_board(tid, bot=bot, source="bot:run_all")
        return

    # --- single-task actions ---
    if action_name in {"create_task", "create_bug"}:
        task_id = raw.get("id") if isinstance(raw, dict) else None
    else:
        # run_next result: {"task": {id, ...}, "message": "..."}
        # advance_task_safely / prepare_task_for_dev result: {"task_id": ..., ...}
        r = raw if isinstance(raw, dict) else {}
        task_id = (
            action_args.get("id")
            or r.get("task_id")
            or (r.get("task") or {}).get("id")
        )

    if task_id:
        await _tb.sync_task_to_board(task_id, bot=bot, source=f"bot:{action_name}")


async def _notify_action_result(context: Any, plan: dict, result: dict) -> None:
    if context is None:
        return
    action_name = (plan.get("action") or {}).get("name", "")
    action_args = (plan.get("action") or {}).get("args") or {}
    r = result.get("result") or {}
    if not isinstance(r, dict):
        r = {}

    if action_name == "create_task":
        await _send_task_card(context, r, "task")
    elif action_name == "create_bug":
        await _send_task_card(context, r, "bug")
    elif action_name in {"run_next", "run_all", "advance_task", "prepare_task"}:
        task_id = action_args.get("task_id") or action_args.get("id") or r.get("task_id") or "?"
        status = r.get("final_status") or r.get("status") or "?"
        await send_status_notification(context, f"✅ Работа по {task_id} завершена\nНовый статус: {status}")

    await _board_sync_after_action(context, action_name, action_args, result)


async def _run_execute(
    update: Any,
    user_text: str,
    confirmed: bool,
    session_id: str,
    user_id: str,
    channel: str,
    context: Any = None,
) -> None:
    if not user_text:
        await _reply(update, "Пустой запрос. Пример: /execute Создай задачу")
        return
    try:
        plan = plan_supervisor_action(user_text, session_id=session_id, user_id=user_id, channel=channel)
    except SupervisorError as exc:
        logging.exception("SupervisorError in _run_execute (planning): %s", exc)
        await _reply(update, "Не смог разобрать ответ Supervisor. Попробуй переформулировать запрос или посмотри логи.")
        return
    if plan.get("intent") in {"clarify", "unknown"}:
        await _reply(update, plan.get("explanation", "Уточни запрос."))
        return
    if plan.get("action", {}).get("name") in RISKY_ACTIONS and not confirmed:
        await _reply(
            update,
            "Действие рискованное и требует подтверждения. Используйте /yes <запрос>.",
        )
        return

    action_name = (plan.get("action") or {}).get("name", "")

    # Capture pre-execution focus so we can restore it after create_task/create_bug.
    # supervisor.execute_supervisor_action shifts focus to the newly created item,
    # but we want to keep the user's existing focus intact.
    pre_focus = get_focus(session_id, user_id=user_id, channel=channel)
    pre_task_id = pre_focus.get("active_task_id")

    try:
        result = execute_supervisor_action(
            plan,
            confirmed=confirmed,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )
    except SupervisorError as exc:
        logging.exception("SupervisorError in _run_execute (execution): %s", exc)
        await _reply(update, f"Ошибка выполнения: {exc}")
        await send_status_notification(
            context,
            f"❌ Ошибка при выполнении действия\nAction: {action_name}\nПричина: {exc}",
        )
        return

    # Build reply text
    reply_text = format_supervisor_execution_result(result, debug=is_debug_mode())

    # Preserve pre-existing focus when a new task/bug was created.
    # Append a short note so the user knows focus was not changed.
    focus_note = ""
    if action_name in {"create_task", "create_bug"} and pre_task_id:
        try:
            set_active_task(session_id, pre_task_id, user_id=user_id, channel=channel)
            focus_note = f"\nТекущий фокус не менял: {pre_task_id}."
        except Exception:
            pass  # pre-focused task may have been removed; silently ignore

    if focus_note:
        reply_text = reply_text + focus_note

    # Append short focus indicator for non-focus actions (where focus is still active).
    if action_name not in _FOCUS_ACTIONS:
        post_focus = get_focus(session_id, user_id=user_id, channel=channel)
        post_task_id = post_focus.get("active_task_id")
        post_task = orchestrator.get_task(post_task_id) if post_task_id else None
        indicator = format_focus_indicator(post_focus, post_task)
        if indicator:
            reply_text = reply_text + f"\n\n{indicator}"

    await _reply(update, reply_text)
    await _notify_action_result(context, plan, result)


async def _try_create_fast(
    update: Any,
    context: Any,
    user_text: str,
    session_id: str,
    user_id: str,
    channel: str,
) -> bool:
    """Try to handle a task/bug creation request without the LLM/Supervisor.

    Returns True when the request was handled (caller should return immediately),
    False when the text did not match a creation pattern and should fall through
    to the normal pipeline.
    """
    detected = telegram_create_router.detect_create_intent(user_text)
    if detected is None:
        # Imperative patterns ("добавить ...", "сделать ...", etc.) only fire when
        # there is no active focus so we don't hijack in-progress task discussions.
        # We call detect_imperative_create_intent FIRST to avoid an unnecessary
        # get_focus() call for messages that don't match any pattern at all.
        imperative_candidate = telegram_create_router.detect_imperative_create_intent(user_text)
        if imperative_candidate is not None:
            focus = get_focus(session_id, user_id=user_id, channel=channel)
            has_focus = bool(
                focus.get("active_task_id")
                or focus.get("active_release_id")
                or focus.get("active_decision_id")
            )
            if not has_focus:
                detected = imperative_candidate
    if detected is None:
        return False

    kind, title = detected

    if not title:
        if kind == "task":
            await _reply(
                update,
                "Не понял название задачи. Напиши, например:\n"
                "\"Создай задачу проверить голосовое управление проектом\"",
            )
        else:
            await _reply(
                update,
                "Не понял название бага. Напиши, например:\n"
                "\"Нашёл баг карточка задачи не обновляется\"",
            )
        return True

    try:
        if kind == "task":
            item = telegram_create_router.create_task_fast(title)
            icon, word = "✅", "задачу"
        else:
            item = telegram_create_router.create_bug_fast(title)
            icon, word = "🐞", "баг"
    except Exception as exc:
        logging.exception("Fast create (%s) failed: %s", kind, exc)
        await _reply(update, f"Ошибка при создании: {exc}")
        return True

    # Board sync — best-effort, never raises
    board_status = "skipped"
    try:
        import telegram_board as _tb
        bot = getattr(context, "bot", None) if context is not None else None
        sync_result = await _tb.sync_task_to_board(
            item["id"], bot=bot, source="bot:fast_create"
        )
        board_status = sync_result.get("status", "skipped")
    except Exception:
        logging.warning("Fast create board sync failed for %s", item.get("id"))

    lines = [
        f"{icon} Создал {word} {item['id']}",
        f"Название: {item.get('title', title)}",
        f"Статус: {item.get('status', 'idea')}",
        f"Board: {board_status}",
    ]
    await _reply(update, "\n".join(lines))

    # Send task card to status chat (best-effort)
    await _send_task_card(context, item, kind)
    return True


async def handle_user_text(
    update: Any,
    context: Any,
    text: str,
    confirmed: bool = False,
    force_execute: bool | None = None,
) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return

    user_text = (text or "").strip()
    if not user_text:
        await _reply(update, "Пустой запрос.")
        return

    session_id, user_id, channel = _telegram_session(update)

    # Focus-switch detection: handle "возьми TASK-X в фокус", "переключись на TASK-X"
    # before the fast router and before the supervisor so it never costs an LLM call.
    focus_switch_id = _check_focus_switch(user_text)
    if focus_switch_id:
        try:
            set_active_task(session_id, focus_switch_id, user_id=user_id, channel=channel)
            task = orchestrator.get_task(focus_switch_id)
            title = (task.get("title", "") if task else "")
            reply = f"🎯 Фокус: {focus_switch_id}"
            if title:
                reply += f" — {title}"
            await _reply(update, reply)
        except Exception:
            await _reply(update, f"Задача {focus_switch_id} не найдена.")
        return

    # Fast router: handle simple read-only queries locally without calling the LLM.
    # The enabled-check lives here (not only inside try_route) so that disabling
    # the flag in tests/config is respected even when try_route is mocked.
    # Reply-to enriched text (containing "Context: user replied to") bypasses the
    # router so the supervisor gets full task context.
    if telegram_fast_router.is_fast_router_enabled() and not text.startswith("Context: user replied to"):
        fast_reply = telegram_fast_router.try_route(user_text)
        if fast_reply is not None:
            await _reply(update, fast_reply)
            return

    # Create router: deterministic task/bug creation without calling LLM.
    # Runs after the fast router (read-only queries) but before the Supervisor.
    # Reply-to enriched messages bypass this so the Supervisor gets full context.
    if not text.startswith("Context: user replied to"):
        if await _try_create_fast(update, context, user_text, session_id, user_id, channel):
            return

    execute_mode = force_execute if force_execute is not None else not cfg["dry_run_by_default"]
    if not execute_mode:
        await _run_dry(update, user_text, session_id=session_id, user_id=user_id, channel=channel, context=context)
        return
    await _run_execute(
        update,
        user_text,
        confirmed=confirmed,
        session_id=session_id,
        user_id=user_id,
        channel=channel,
        context=context,
    )


async def start_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return

    from managed_project import get_managed_project_info
    info = get_managed_project_info()
    managed_path = info.get("managed_repo_path", ".")

    if managed_path == ".":
        mode_text = (
            "⚠️ Сейчас я работаю над самим Team of Agents.\n"
            "Чтобы подключить внешний проект, укажи MANAGED_REPO_PATH в .env."
        )
    else:
        mode_text = "✅ Подключён к внешнему проекту."

    session_id, user_id, channel = _telegram_session(update)
    focus = get_focus(session_id, user_id=user_id, channel=channel)
    focus_line = format_focus_indicator(focus)
    focus_block = f"\n\n{focus_line}" if focus_line else ""

    text = (
        "👋 Project Manager Bot\n\n"
        f"{mode_text}\n\n"
        "Что можно делать:\n"
        "• Создай задачу: ...\n"
        "• У нас баг: ...\n"
        "• Что делать дальше?\n"
        "• Статус проекта\n\n"
        "Напиши мне что нужно сделать, или используй /help."
        f"{focus_block}"
    )

    message = getattr(update, "message", None)
    if message is None:
        return
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔍 Изучить проект", callback_data="study_project"),
        ]])
        await message.reply_text(text, reply_markup=keyboard)
    except ImportError:
        await message.reply_text(truncate_text(text))


async def help_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    text = (
        "Ты можешь писать обычным языком — не только slash-командами.\n"
        "Supervisor разберёт смысл и предложит или выполнит нужное действие.\n\n"
        "Подтверждение действий:\n"
        "• Dry-run показывает кнопки ✅ Выполнить / ❌ Отмена\n"
        "• Рискованные действия требуют /yes <запрос>\n\n"
        "Уведомления:\n"
        "• Если задан TELEGRAM_STATUS_CHAT_ID — события агентов приходят в отдельный чат\n\n"
        "Slash-команды:\n"
        "/start        — режим проекта + кнопка 'Изучить проект'\n"
        "/help         — эта справка\n"
        "/status       — Статус проекта\n"
        "/board_config — конфигурация Telegram Board\n"
        "/board_ping   — smoke-test: отправить ping во все топики Board\n"
        "/board_sync   — синхронизировать все задачи на Board\n"
        "/actions      — список поддерживаемых действий\n"
        "/dryrun <текст>  — только план, без выполнения\n"
        "/execute <текст> — план + выполнение\n"
        "/yes <текст>     — подтверждение рискованного действия\n"
        "/focus\n/focus_task TASK-1\n/focus_release REL-001\n/focus_decision ADR-001\n/clear_focus\n\n"
        "Примеры (обычный текст):\n"
        "- Создай задачу: проверить Telegram Project Manager\n"
        "- У нас баг: validate падает с FileNotFoundError\n"
        "- Что делать дальше?\n"
        "- Статус проекта\n"
        "- Покажи статус проекта\n"
        "- Подготовь TASK-1 к разработке\n"
        "- Добавь заметку к TASK-1: нужно проверить edge case\n"
        "- Что заблокировано?\n"
        "- Дай статус релиза REL-001\n"
        "- Каким проектом ты управляешь?\n"
        "- Обсудим TASK-1\n"
        "- Добавь заметку: проверить edge case\n"
        "- Что по ней сейчас?\n"
        "- Сбрось фокус\n\n"
        "Правило безопасности: рискованные действия выполняются только через /yes."
    )
    await _reply(update, text)


async def board_config_handler(update: Any, context: Any) -> None:
    """Show Telegram Board configuration status. Owner-only."""
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    import telegram_board
    text = telegram_board.format_board_config_status()
    await _reply(update, text)


async def board_ping_handler(update: Any, context: Any) -> None:
    """Send a ping message to every configured board topic. Owner-only."""
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    import telegram_board
    try:
        results = await telegram_board.ping_board_topics(context.bot)
    except ValueError as exc:
        await _reply(update, str(exc))
        return
    text = telegram_board.format_ping_results(results)
    await _reply(update, text)


async def board_sync_handler(update: Any, context: Any) -> None:
    """Sync all task cards to the Telegram Board. Owner-only."""
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    import telegram_board
    await _reply(update, "Синхронизирую Board...")
    result = await telegram_board.sync_all_tasks_to_board(
        bot=context.bot, source="bot:/board_sync"
    )
    text = telegram_board.format_board_sync_summary(result)
    await _reply(update, text)


async def status_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return

    tasks = orchestrator.list_tasks()
    ready_tasks = backlog.get_ready_tasks(tasks)
    blocked_tasks = backlog.get_blocked_tasks(tasks)
    releases = load_releases()
    provider = (os.getenv("LLM_PROVIDER") or "fake").strip().lower()
    pm = get_project_status()
    try:
        managed = validate_managed_repo_path()
        same_root_warning = (
            " (warning: совпадает с system root)" if managed["managed_repo_root"] == managed["system_root"] else ""
        )
    except Exception:
        managed = {
            "managed_repo_path": "unknown",
            "managed_repo_root": "unknown",
        }
        same_root_warning = ""
    session_id, user_id, channel = _telegram_session(update)
    focus = get_focus(session_id, user_id=user_id, channel=channel)

    text = (
        "Статус системы:\n"
        f"- LLM_PROVIDER: {provider}\n"
        f"- dry-run по умолчанию: {'да' if cfg['dry_run_by_default'] else 'нет'}\n"
        f"- managed path: {managed['managed_repo_path']}\n"
        f"- managed root: {managed['managed_repo_root']}{same_root_warning}\n"
        f"- задач: {len(tasks)}\n"
        f"- ready: {len(ready_tasks)}\n"
        f"- blocked: {len(blocked_tasks)}\n"
        f"- релизов: {len(releases)}\n"
        f"- focus: {focus.get('summary')}\n"
        f"- PM summary: {pm.get('message', '')}"
    )
    await _reply(update, text)


async def actions_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return

    read_only = [item for item in sorted(SUPPORTED_ACTIONS) if item in READ_ONLY_ACTIONS]
    write = [item for item in sorted(SUPPORTED_ACTIONS) if item not in READ_ONLY_ACTIONS and item not in RISKY_ACTIONS]
    risky = [item for item in sorted(SUPPORTED_ACTIONS) if item in RISKY_ACTIONS]

    text = (
        "Поддерживаемые действия Supervisor:\n\n"
        f"Read-only: {', '.join(read_only)}\n\n"
        f"Write: {', '.join(write)}\n\n"
        f"Risky: {', '.join(risky)}"
    )
    await _reply(update, text)


async def dryrun_handler(update: Any, context: Any) -> None:
    await handle_user_text(update, context, _extract_text(context, update), confirmed=False, force_execute=False)


async def execute_handler(update: Any, context: Any) -> None:
    await handle_user_text(update, context, _extract_text(context, update), confirmed=False, force_execute=True)


async def yes_handler(update: Any, context: Any) -> None:
    await handle_user_text(update, context, _extract_text(context, update), confirmed=True, force_execute=True)


async def focus_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    session_id, user_id, channel = _telegram_session(update)

    # /focus TASK-X → set focus (same as /focus_task TASK-X)
    task_id = _extract_text(context, update).strip().upper()
    if task_id:
        try:
            set_active_task(session_id, task_id, user_id=user_id, channel=channel)
        except Exception:
            await _reply(update, f"Задача {task_id} не найдена.")
            return

    focus = get_focus(session_id, user_id=user_id, channel=channel)
    await _reply(update, format_focus(focus))


async def focus_task_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    task_id = _extract_text(context, update).strip().upper()
    if not task_id:
        await _reply(update, "Укажи ID задачи: /focus_task TASK-1")
        return
    session_id, user_id, channel = _telegram_session(update)
    try:
        session = set_active_task(session_id, task_id, user_id=user_id, channel=channel)
    except Exception:
        await _reply(update, f"Задача {task_id} не найдена.")
        return
    await _reply(update, format_focus(get_focus(session["session_id"], user_id=user_id, channel=channel)))


async def focus_release_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    release_id = _extract_text(context, update).strip().upper()
    if not release_id:
        await _reply(update, "Укажи ID релиза: /focus_release REL-001")
        return
    session_id, user_id, channel = _telegram_session(update)
    set_active_release(session_id, release_id, user_id=user_id, channel=channel)
    await _reply(update, format_focus(get_focus(session_id, user_id=user_id, channel=channel)))


async def focus_decision_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    decision_id = _extract_text(context, update).strip().upper()
    if not decision_id:
        await _reply(update, "Укажи ID решения: /focus_decision ADR-001")
        return
    session_id, user_id, channel = _telegram_session(update)
    set_active_decision(session_id, decision_id, user_id=user_id, channel=channel)
    await _reply(update, format_focus(get_focus(session_id, user_id=user_id, channel=channel)))


async def clear_focus_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Access denied.")
        return
    session_id, user_id, channel = _telegram_session(update)
    pre_focus = get_focus(session_id, user_id=user_id, channel=channel)
    had_focus = bool(
        pre_focus.get("active_task_id")
        or pre_focus.get("active_release_id")
        or pre_focus.get("active_decision_id")
    )
    clear_focus(session_id, user_id=user_id, channel=channel)
    if had_focus:
        await _reply(update, "Фокус снят. Можно создавать новые задачи или выбрать другую.")
    else:
        await _reply(update, "Сейчас нет активного фокуса.")


async def text_handler(update: Any, context: Any) -> None:
    user_text = (getattr(update.message, "text", "") or "").strip()
    if not user_text:
        return

    # Reply-based task context: resolve message -> work item locally (no LLM call)
    # before deciding how to route. This keeps token usage minimal for simple replies.
    reply_to = getattr(update.message, "reply_to_message", None)
    if reply_to is not None:
        chat_id = str(getattr(update.effective_chat, "id", ""))
        msg_id = getattr(reply_to, "message_id", None)
        status_chat_id = get_status_chat_id()

        if msg_id is not None:
            link = telegram_message_links.find_link(chat_id, msg_id)
            if link is not None:
                # Enrich the user message with task/bug context for the supervisor.
                # This lookup is done locally (no LLM call) to keep token usage minimal.
                work_id = link["work_item_id"]
                work_type = link.get("work_item_type", "task")
                enriched = (
                    f"Context: user replied to {work_id} ({work_type}). "
                    f"User message: \"{user_text}\""
                )
                await _reply(update, f"Понял, работаю с {work_id}.")
                await handle_user_text(update, context, enriched, confirmed=False, force_execute=None)
                return
            if status_chat_id and chat_id == str(status_chat_id):
                # Reply in the status chat to an unknown message – give friendly hint
                await _reply(
                    update,
                    "Не нашёл связанную задачу для этого сообщения. "
                    "Попробуй ответить на карточку задачи/бага.",
                )
                return

    await handle_user_text(update, context, user_text, confirmed=False, force_execute=None)


async def voice_handler(update: Any, context: Any) -> None:
    cfg = context.bot_data["telegram_config"]
    if not is_owner(update, cfg["owner_id"]):
        await _reply(update, "Доступ запрещён.")
        return
    if not is_voice_enabled():
        await _reply(
            update,
            "Голосовой ввод пока выключен. "
            "Включи STT_PROVIDER=whisper_cli или STT_PROVIDER=custom_cli.",
        )
        return

    message = getattr(update, "message", None)
    voice = getattr(message, "voice", None) if message else None
    if voice is None or not getattr(voice, "file_id", None):
        await _reply(update, "Не удалось получить голосовое сообщение.")
        return

    temp_files: list[str] = []
    transcript: str = ""
    stt_ok: bool = False
    try:
        work_dir = ensure_voice_work_dir()
        stem = f"voice_{uuid.uuid4().hex}"
        input_path = work_dir / f"{stem}.ogg"
        wav_path = work_dir / f"{stem}.wav"
        temp_files.extend([input_path.as_posix(), wav_path.as_posix()])

        tg_file = await context.bot.get_file(voice.file_id)
        await tg_file.download_to_drive(custom_path=input_path.as_posix())

        await _reply(update, "🎙 Принял голосовое. Расшифровываю...")

        convert_voice_to_wav(input_path.as_posix(), wav_path.as_posix())
        transcript = transcribe_audio(wav_path.as_posix())
        stt_ok = True

        if not transcript.strip():
            await _reply(update, "Не смог распознать текст в голосовом.")
            return

        await _reply(update, f"🎙 Распознал:\n{truncate_text(transcript, limit=300)}")

        try:
            await handle_user_text(update, context, transcript, confirmed=False, force_execute=None)
        except Exception as downstream_exc:
            logging.exception("Voice downstream error after successful STT: %s", downstream_exc)
            error_id, log_path = write_error_log(
                downstream_exc, update=update, handler_name="voice_handler:downstream"
            )
            await _reply(
                update,
                f"Голос распознан, но не удалось обработать команду: {downstream_exc}\n"
                f"Error ID: {error_id}\n"
                f"Лог: {log_path}",
            )

    except SpeechToTextError as exc:
        await _reply(update, f"Ошибка голосового ввода: {exc}")
    except Exception as exc:
        if stt_ok:
            logging.exception("Voice post-STT error: %s", exc)
            error_id, log_path = write_error_log(
                exc, update=update, handler_name="voice_handler:post_stt"
            )
            await _reply(
                update,
                f"Голос распознан, но не удалось обработать команду: {exc}\n"
                f"Error ID: {error_id}\n"
                f"Лог: {log_path}",
            )
        else:
            logging.exception("Voice STT/processing error: %s", exc)
            await _reply(update, f"Ошибка обработки голосового сообщения: {exc}")
    finally:
        if not should_keep_voice_files():
            cleanup_voice_files(temp_files)


async def confirm_callback(update: Any, context: Any) -> None:
    query = update.callback_query
    await query.answer()
    owner_id = (context.bot_data.get("telegram_config") or {}).get("owner_id", "")
    if not is_owner(update, owner_id):
        await query.edit_message_text("Доступ запрещён.")
        return
    session_id, user_id, channel = _telegram_session(update)
    pending = PENDING_ACTIONS.get(session_id)
    if not pending:
        await query.edit_message_text("Нет действия для подтверждения. Отправь запрос ещё раз.")
        return
    plan = pending["plan"]
    if plan.get("requires_confirmation"):
        await query.edit_message_text(
            "Это рискованное действие. Для подтверждения используй /yes <исходный запрос>."
        )
        return
    del PENDING_ACTIONS[session_id]
    try:
        result = execute_supervisor_action(
            plan, confirmed=False, session_id=session_id, user_id=user_id, channel=channel
        )
    except SupervisorError as exc:
        logging.exception("SupervisorError in confirm_callback: %s", exc)
        await query.edit_message_text(f"Ошибка выполнения: {exc}")
        await send_status_notification(context, f"❌ Ошибка: {exc}")
        return
    await query.edit_message_text(truncate_text(format_supervisor_execution_result(result, debug=is_debug_mode())))
    await _notify_action_result(context, plan, result)


async def cancel_callback(update: Any, context: Any) -> None:
    query = update.callback_query
    await query.answer()
    session_id, _, _ = _telegram_session(update)
    PENDING_ACTIONS.pop(session_id, None)
    await query.edit_message_text("Действие отменено.")


async def board_focus_callback(update: Any, context: Any) -> None:
    """Inline-button handler: set focus on a Board task card (🎯 В фокус)."""
    query = update.callback_query
    await query.answer()
    owner_id = (context.bot_data.get("telegram_config") or {}).get("owner_id", "")
    if not is_owner(update, owner_id):
        # Non-owner clicks are silently ignored — no edit to the public card
        return

    import telegram_board
    parsed = telegram_board.parse_board_task_callback(query.data)
    if not parsed:
        return

    task_id = parsed["task_id"]
    session_id, user_id, channel = _telegram_session(update)
    set_active_task(session_id, task_id, user_id=user_id, channel=channel)

    # Send private DM to owner with confirmation and usage hint
    try:
        task = orchestrator.get_task(task_id)
        title = (task.get("title", "") or "") if task else ""
        status_raw = (task.get("status", "") if task else "")
        status_label = _FOCUS_STATUS_LABELS.get(status_raw, status_raw)

        dm_lines = [f"🎯 Фокус: {task_id}"]
        if title:
            dm_lines.append(f"Название: {title}")
        if status_label:
            dm_lines.append(f"Статус: {status_label}")
        dm_lines.append("")
        dm_lines.append("Пиши сюда, что нужно сделать по этой задаче. Чтобы снять фокус: /clear_focus")
        await context.bot.send_message(chat_id=update.effective_user.id, text="\n".join(dm_lines))
    except Exception:
        logging.exception("board_focus_callback: failed to send DM for task %s", task_id)


async def board_start_callback(update: Any, context: Any) -> None:
    """Inline-button handler: start a ready_for_dev task (🚧 В работу)."""
    query = update.callback_query
    await query.answer()
    owner_id = (context.bot_data.get("telegram_config") or {}).get("owner_id", "")
    if not is_owner(update, owner_id):
        return

    import telegram_board
    parsed = telegram_board.parse_board_task_callback(query.data)
    if not parsed:
        return

    task_id = parsed["task_id"]

    # Load, mutate, save
    tasks = orchestrator.load_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if task is None:
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"Задача {task_id} не найдена.",
            )
        except Exception:
            pass
        return

    current_status = task.get("status", "")
    if current_status == "in_progress":
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"Задача {task_id} уже в работе.",
            )
        except Exception:
            pass
        return

    task["status"] = "in_progress"
    orchestrator.save_tasks(tasks)

    # Set focus
    session_id, user_id, channel = _telegram_session(update)
    set_active_task(session_id, task_id, user_id=user_id, channel=channel)

    # Upsert board card (moves card from task_ready → task_active topic)
    try:
        await telegram_board.upsert_task_board_card(context.bot, task)
    except Exception:
        logging.exception("board_start_callback: failed to upsert board card for task %s", task_id)

    # Send private DM to owner
    title = (task.get("title", "") or "")
    dm_text = f"🚧 Задача {task_id} взята в работу"
    if title:
        dm_text += f": {title}"
    try:
        await context.bot.send_message(chat_id=update.effective_user.id, text=dm_text)
    except Exception:
        logging.exception("board_start_callback: failed to send DM for task %s", task_id)


async def study_project_callback(update: Any, context: Any) -> None:
    query = update.callback_query
    await query.answer()
    owner_id = (context.bot_data.get("telegram_config") or {}).get("owner_id", "")
    if not is_owner(update, owner_id):
        await query.edit_message_text("Доступ запрещён.")
        return

    await query.edit_message_text("Начинаю изучение проекта...")
    await send_status_notification(context, "🔍 Начато изучение проекта")

    from managed_project import get_managed_project_info, validate_managed_repo_path as _validate_mrp
    from repo_inspector import scan_repository

    info = get_managed_project_info()
    managed_path = info.get("managed_repo_path", ".")
    managed_root = info.get("managed_repo_root", "?")
    chat_id = update.effective_chat.id

    if managed_path == ".":
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "ℹ️ Сейчас я изучаю сам Team-of-agents, потому что MANAGED_REPO_PATH=.\n"
                "Для внешнего проекта укажи MANAGED_REPO_PATH=.."
            ),
        )
        return

    validation = _validate_mrp()
    if validation.get("errors"):
        errors_text = "\n".join(validation["errors"])
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Team of Agents не добавлен в проект или MANAGED_REPO_PATH неверный.\n"
                f"Путь: {managed_root}\nОшибки:\n{errors_text}"
            ),
        )
        return

    try:
        scan = scan_repository(repo_root=managed_root)
        total = scan.get("total_files_indexed", "?")
        warnings = validation.get("warnings", [])
        warn_text = ("\n⚠️ " + "\n⚠️ ".join(warnings)) if warnings else ""
        result_text = (
            f"✅ Проект изучен\n"
            f"Managed repo: {managed_root}\n"
            f"Файлов: {total}{warn_text}\n\n"
            "Что дальше:\n"
            "• Отправь задачу: 'Создай задачу ...'\n"
            "• Посмотри статус: /status\n"
            "• Прикрепи контекст задачи: 'attach context to TASK-1'"
        )
    except Exception as exc:
        result_text = f"Ошибка при сканировании: {exc}"

    await context.bot.send_message(chat_id=chat_id, text=result_text)


async def error_handler(update: Any, context: Any) -> None:
    exc = getattr(context, "error", None)
    logging.exception("Unhandled Telegram error", exc_info=exc)

    if exc is None:
        return

    error_id, log_path = write_error_log(exc, update=update, handler_name="error_handler")

    message = getattr(update, "message", None) if update is not None else None
    if message is not None:
        try:
            if _is_empty_output_error(exc):
                user_msg = (
                    "Claude Code вернул пустой ответ. "
                    "Возможно, закончились лимиты или CLI не смог выполнить запрос.\n"
                    f"Error ID: {error_id}\n"
                    f"Лог: {log_path}"
                )
            else:
                user_msg = (
                    "Произошла внутренняя ошибка.\n"
                    f"Error ID: {error_id}\n"
                    f"Лог: {log_path}"
                )
            await message.reply_text(user_msg)
        except Exception:
            pass


def build_application(config: dict) -> Any:
    try:
        from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    except ImportError as exc:
        raise RuntimeError(
            "python-telegram-bot is not installed. Install dependencies from requirements.txt."
        ) from exc

    app = ApplicationBuilder().token(config["token"]).build()
    app.bot_data["telegram_config"] = config

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("board_config", board_config_handler))
    app.add_handler(CommandHandler("board_ping", board_ping_handler))
    app.add_handler(CommandHandler("board_sync", board_sync_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("actions", actions_handler))
    app.add_handler(CommandHandler("dryrun", dryrun_handler))
    app.add_handler(CommandHandler("execute", execute_handler))
    app.add_handler(CommandHandler("yes", yes_handler))
    app.add_handler(CommandHandler("focus", focus_handler))
    app.add_handler(CommandHandler("focus_task", focus_task_handler))
    app.add_handler(CommandHandler("focus_release", focus_release_handler))
    app.add_handler(CommandHandler("focus_decision", focus_decision_handler))
    app.add_handler(CommandHandler("clear_focus", clear_focus_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm_pending_action$"))
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern="^cancel_pending_action$"))
    app.add_handler(CallbackQueryHandler(study_project_callback, pattern="^study_project$"))
    app.add_handler(CallbackQueryHandler(board_focus_callback, pattern=r"^board:task:focus:"))
    app.add_handler(CallbackQueryHandler(board_start_callback, pattern=r"^board:task:start:"))
    app.add_error_handler(error_handler)

    return app


def start_polling_bot() -> None:
    config = load_telegram_config()
    app = build_application(config)
    app.run_polling()
