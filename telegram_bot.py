from __future__ import annotations

import datetime
import json
import logging
import os
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


def format_focus(focus: dict) -> str:
    return (
        "Текущий фокус:\n"
        f"- task: {focus.get('active_task_id')}\n"
        f"- release: {focus.get('active_release_id')}\n"
        f"- decision: {focus.get('active_decision_id')}\n"
        f"- summary: {focus.get('summary')}"
    )


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
    icon = "🧩" if work_item_type == "task" else "🐞"
    item_id = item.get("id", "?")
    title = item.get("title", "?")
    status = item.get("status", "?")
    priority = item.get("priority", "medium")
    severity = item.get("severity", "")
    description = truncate_text(item.get("description", "") or "", limit=200)

    lines = [f"{icon} {item_id}: {title}", f"Статус: {status}"]
    if severity and severity != "unknown":
        lines.append(f"Серьёзность: {severity}")
    else:
        lines.append(f"Приоритет: {priority}")
    if description:
        lines.append(f"Описание: {description}")
    lines.extend(["—", "Ответь на это сообщение, чтобы работать с задачей."])
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
        action_name = (plan.get("action") or {}).get("name", "?")
        await send_status_notification(
            context,
            f"❌ Ошибка при выполнении действия\nAction: {action_name}\nПричина: {exc}",
        )
        return
    await _reply(update, format_supervisor_execution_result(result, debug=is_debug_mode()))
    await _notify_action_result(context, plan, result)


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

    text = (
        "👋 Project Manager Bot\n\n"
        f"{mode_text}\n\n"
        "Что можно делать:\n"
        "• Создай задачу: ...\n"
        "• У нас баг: ...\n"
        "• Что делать дальше?\n"
        "• Статус проекта\n\n"
        "Напиши мне что нужно сделать, или используй /help."
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
        "/start   — режим проекта + кнопка 'Изучить проект'\n"
        "/help    — эта справка\n"
        "/status  — Статус проекта\n"
        "/actions — список поддерживаемых действий\n"
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
    await _reply(update, format_focus(get_focus(session_id, user_id=user_id, channel=channel)))


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
    session = set_active_task(session_id, task_id, user_id=user_id, channel=channel)
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
    clear_focus(session_id, user_id=user_id, channel=channel)
    await _reply(update, format_focus(get_focus(session_id, user_id=user_id, channel=channel)))


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
                # Enrich the user message with task/bug context for the supervisor
                work_id = link["work_item_id"]
                work_type = link.get("work_item_type", "task")
                enriched = (
                    f"Context: user replied to {work_id} ({work_type}). "
                    f"User message: \"{user_text}\""
                )
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
        await _reply(update, "Access denied.")
        return
    if not is_voice_enabled():
        await _reply(update, "Голосовой ввод пока выключен. Настрой STT_PROVIDER.")
        return

    message = getattr(update, "message", None)
    voice = getattr(message, "voice", None) if message else None
    if voice is None or not getattr(voice, "file_id", None):
        await _reply(update, "Не удалось получить голосовое сообщение.")
        return

    temp_files: list[str] = []
    try:
        work_dir = ensure_voice_work_dir()
        stem = f"voice_{uuid.uuid4().hex}"
        input_path = work_dir / f"{stem}.ogg"
        wav_path = work_dir / f"{stem}.wav"
        temp_files.extend([input_path.as_posix(), wav_path.as_posix()])

        tg_file = await context.bot.get_file(voice.file_id)
        await tg_file.download_to_drive(custom_path=input_path.as_posix())

        convert_voice_to_wav(input_path.as_posix(), wav_path.as_posix())
        transcript = transcribe_audio(wav_path.as_posix())
        await _reply(update, f"Распознал голос: {truncate_text(transcript, limit=300)}")
        await handle_user_text(update, context, transcript, confirmed=False, force_execute=None)
    except SpeechToTextError as exc:
        await _reply(update, f"Ошибка голосового ввода: {exc}")
    except Exception as exc:  # pragma: no cover
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
    import logging
    logging.exception("Unhandled Telegram error", exc_info=getattr(context, "error", None))
    message = getattr(update, "message", None) if update is not None else None
    if message is not None:
        try:
            await message.reply_text("Произошла внутренняя ошибка. Я её залогировал.")
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
    app.add_error_handler(error_handler)

    return app


def start_polling_bot() -> None:
    config = load_telegram_config()
    app = build_application(config)
    app.run_polling()
