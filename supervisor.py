from __future__ import annotations

import json
import os
from pathlib import Path

import backlog
import orchestrator
from command_runner import is_command_allowed, run_safe_command
from conversation_context import (
    append_message,
    clear_focus,
    get_focus,
    get_or_create_session,
    resolve_reference,
    save_session,
    set_active_decision,
    set_active_release,
    set_active_task,
)
from decision_log import (
    create_decision,
    get_decision_by_id,
    get_task_related_decisions,
    link_decision_to_task,
    list_decisions,
    unlink_decision_from_task,
)
from llm_client import LLMClientError, get_llm_client
from managed_project import get_managed_project_info, validate_managed_repo_path
from patch_utils import apply_patch_proposal, approve_patch
from project_manager import (
    add_task_note,
    advance_task_safely,
    get_blockers_summary,
    get_next_work_recommendation,
    get_project_status,
    get_release_summary,
    get_task_status,
    list_task_notes,
    prepare_task_for_development,
    summarize_task_discussion,
)
from project_context_loader import load_project_context, load_project_context_text, list_project_context_files
from release_manager import (
    ALLOWED_RELEASE_STATUSES,
    add_task_to_release,
    calculate_release_readiness,
    create_release,
    generate_release_notes,
    generate_release_risks,
    generate_rollback_plan,
    get_release_by_id,
    load_releases,
    remove_task_from_release,
    save_releases,
    set_release_status,
)
from repo_inspector import build_repository_context_for_task, list_repository_tree, read_repository_file, scan_repository, search_repository

SUPERVISOR_PROMPT_PATH = Path("agents") / "supervisor.md"

SUPPORTED_ACTIONS = {
    "list_tasks",
    "show_task",
    "backlog",
    "ready",
    "blocked",
    "next_task",
    "list_releases",
    "show_release",
    "release_readiness",
    "release_notes",
    "release_risks",
    "rollback_plan",
    "list_decisions",
    "show_decision",
    "task_decisions",
    "repo_scan",
    "repo_tree",
    "repo_search",
    "repo_file",
    "context",
    "agents",
    "config",
    "managed_project",
    "managed_project_check",
    "project_status",
    "task_status",
    "release_summary",
    "next_work",
    "blockers_summary",
    "task_notes",
    "summarize_task_discussion",
    "prepare_task_for_dev",
    "advance_task_safely",
    "add_task_note",
    "focus",
    "clear_focus",
    "set_focus_task",
    "set_focus_release",
    "set_focus_decision",
    "create_task",
    "create_bug",
    "run_next",
    "run_all",
    "add_dependency",
    "remove_dependency",
    "block_task",
    "unblock_task",
    "attach_repo_context",
    "create_decision",
    "link_decision",
    "unlink_decision",
    "create_release",
    "add_to_release",
    "remove_from_release",
    "set_release_status",
    "approve_patch",
    "apply_patch",
    "run_command",
    "run_plan_commands",
}

READ_ONLY_ACTIONS = {
    "list_tasks",
    "show_task",
    "backlog",
    "ready",
    "blocked",
    "next_task",
    "list_releases",
    "show_release",
    "release_readiness",
    "release_notes",
    "release_risks",
    "rollback_plan",
    "list_decisions",
    "show_decision",
    "task_decisions",
    "repo_scan",
    "repo_tree",
    "repo_search",
    "repo_file",
    "context",
    "agents",
    "config",
    "managed_project",
    "managed_project_check",
    "project_status",
    "task_status",
    "release_summary",
    "next_work",
    "blockers_summary",
    "task_notes",
    "summarize_task_discussion",
    "focus",
    "clear_focus",
}

RISKY_ACTIONS = {"apply_patch", "run_command", "run_plan_commands", "run_all", "set_release_status"}

IMPLEMENTED_EXECUTION_ACTIONS = {
    "agents",
    "config",
    "managed_project",
    "managed_project_check",
    "context",
    "create_task",
    "create_bug",
    "list_tasks",
    "show_task",
    "backlog",
    "ready",
    "blocked",
    "next_task",
    "run_next",
    "attach_repo_context",
    "list_releases",
    "show_release",
    "release_readiness",
    "release_notes",
    "release_risks",
    "rollback_plan",
    "list_decisions",
    "show_decision",
    "task_decisions",
    "repo_scan",
    "repo_tree",
    "repo_search",
    "repo_file",
    "create_release",
    "add_to_release",
    "remove_from_release",
    "set_release_status",
    "create_decision",
    "link_decision",
    "unlink_decision",
    "add_dependency",
    "remove_dependency",
    "block_task",
    "unblock_task",
    "approve_patch",
    "apply_patch",
    "run_all",
    "run_command",
    "run_plan_commands",
    "project_status",
    "task_status",
    "release_summary",
    "next_work",
    "blockers_summary",
    "task_notes",
    "summarize_task_discussion",
    "prepare_task_for_dev",
    "advance_task_safely",
    "add_task_note",
    "focus",
    "clear_focus",
    "set_focus_task",
    "set_focus_release",
    "set_focus_decision",
}


class SupervisorError(Exception):
    pass


def validate_supervisor_output(output: dict) -> None:
    if not isinstance(output, dict):
        raise SupervisorError("Supervisor output must be an object.")
    for field in ("intent", "confidence", "requires_confirmation", "action", "explanation", "warnings"):
        if field not in output:
            raise SupervisorError(f"Supervisor output missing field: {field}")
    if not isinstance(output["intent"], str):
        raise SupervisorError("Supervisor output intent must be a string.")
    if not isinstance(output["confidence"], (int, float)) or not (0 <= float(output["confidence"]) <= 1):
        raise SupervisorError("Supervisor output confidence must be between 0 and 1.")
    if not isinstance(output["requires_confirmation"], bool):
        raise SupervisorError("Supervisor output requires_confirmation must be boolean.")
    if not isinstance(output["action"], dict):
        raise SupervisorError("Supervisor output action must be an object.")
    if not isinstance(output["explanation"], str):
        raise SupervisorError("Supervisor output explanation must be a string.")
    if not isinstance(output["warnings"], list):
        raise SupervisorError("Supervisor output warnings must be a list.")

    action_name = output["action"].get("name")
    action_args = output["action"].get("args")
    if not isinstance(action_name, str):
        raise SupervisorError("Supervisor output action.name must be a string.")
    if not isinstance(action_args, dict):
        raise SupervisorError("Supervisor output action.args must be an object.")

    if output["intent"] not in {"clarify", "unknown"} and action_name not in SUPPORTED_ACTIONS:
        raise SupervisorError(f"Unsupported supervisor action: {action_name}")
    if action_name in RISKY_ACTIONS and output["requires_confirmation"] is not True:
        raise SupervisorError(f"Risky action '{action_name}' must require confirmation.")


def build_supervisor_payload(
    user_text: str,
    session_id: str = "cli:default",
    user_id: str = "",
    channel: str = "cli",
) -> dict:
    session = get_or_create_session(session_id=session_id, user_id=user_id, channel=channel)
    focus = get_focus(session_id=session_id, user_id=user_id, channel=channel)
    recent_preview = (session.get("recent_messages") or [])[-6:]
    return {
        "agent_name": "supervisor",
        "user_text": user_text,
        "session_id": session_id,
        "session_focus": focus,
        "recent_messages_preview": recent_preview,
        "supported_actions": sorted(SUPPORTED_ACTIONS),
        "read_only_actions": sorted(READ_ONLY_ACTIONS),
        "risky_actions": sorted(RISKY_ACTIONS),
        "safety_rules": [
            "Never execute actions directly.",
            "Never expose secrets.",
            "No arbitrary shell commands.",
            "No bypass of orchestrator workflow rules.",
        ],
        "project_context": load_project_context_text(),
        "strict_output_schema": {
            "intent": "string",
            "confidence": "0..1",
            "requires_confirmation": "bool",
            "action": {"name": "string", "args": "object"},
            "explanation": "string",
            "warnings": ["string"],
        },
    }


def plan_supervisor_action(
    user_text: str,
    llm_client=None,
    session_id: str = "cli:default",
    user_id: str = "",
    channel: str = "cli",
) -> dict:
    if not SUPERVISOR_PROMPT_PATH.exists():
        raise SupervisorError(f"Supervisor prompt file is missing: {SUPERVISOR_PROMPT_PATH.as_posix()}")
    prompt = SUPERVISOR_PROMPT_PATH.read_text(encoding="utf-8")
    payload = build_supervisor_payload(user_text, session_id=session_id, user_id=user_id, channel=channel)
    payload["agent_prompt"] = prompt
    client = llm_client or get_llm_client()
    raw = client.generate(payload)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SupervisorError(f"Supervisor returned invalid JSON: {exc}. Raw output: {raw}") from exc
    validate_supervisor_output(parsed)
    parsed["user_text"] = user_text
    append_message(session_id, role="user", text=str(user_text), user_id=user_id, channel=channel)
    return parsed


def _load_task_for_update(task_id: str) -> tuple[list[dict], dict]:
    tasks = orchestrator.load_tasks()
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise SupervisorError(f"Task not found: {task_id}")
    return tasks, task


def _resolve_missing_id_args(
    action_name: str,
    args: dict,
    user_text: str,
    session_id: str,
    user_id: str,
    channel: str,
) -> dict:
    session = get_or_create_session(session_id=session_id, user_id=user_id, channel=channel)
    resolved = dict(args or {})
    ref = resolve_reference(user_text, session)
    task_actions = {
        "show_task",
        "task_status",
        "task_notes",
        "summarize_task_discussion",
        "prepare_task_for_dev",
        "advance_task_safely",
        "add_task_note",
    }
    release_actions = {"show_release", "release_summary", "release_readiness", "release_notes", "release_risks", "rollback_plan"}
    decision_actions = {"show_decision"}

    if action_name in task_actions and not resolved.get("id"):
        if ref.get("task_id"):
            resolved["id"] = ref["task_id"]
        elif session.get("active_task_id"):
            resolved["id"] = session["active_task_id"]
        else:
            raise SupervisorError("Не понял, к какой задаче это относится. Укажи TASK-... или установи фокус.")

    if action_name in release_actions and not resolved.get("id"):
        if ref.get("release_id"):
            resolved["id"] = ref["release_id"]
        elif session.get("active_release_id"):
            resolved["id"] = session["active_release_id"]
        else:
            raise SupervisorError("Не понял, к какому релизу это относится. Укажи REL-... или установи фокус.")

    if action_name in decision_actions and not resolved.get("id"):
        if ref.get("decision_id"):
            resolved["id"] = ref["decision_id"]
        elif session.get("active_decision_id"):
            resolved["id"] = session["active_decision_id"]
        else:
            raise SupervisorError("Не понял, к какому решению это относится. Укажи ADR-... или установи фокус.")
    return resolved


def execute_supervisor_action(
    plan: dict,
    confirmed: bool = False,
    session_id: str = "cli:default",
    user_id: str = "",
    channel: str = "cli",
) -> dict:
    validate_supervisor_output(plan)
    intent = plan["intent"]
    if intent in {"clarify", "unknown"}:
        return {"executed": False, "message": plan["explanation"], "plan": plan}

    action_name = plan["action"]["name"]
    args = _resolve_missing_id_args(
        action_name=action_name,
        args=plan["action"]["args"],
        user_text=str(plan.get("user_text", "")),
        session_id=session_id,
        user_id=user_id,
        channel=channel,
    )

    if action_name in RISKY_ACTIONS and not confirmed:
        raise SupervisorError(f"Action '{action_name}' is risky and requires explicit confirmation.")

    if action_name not in IMPLEMENTED_EXECUTION_ACTIONS:
        return {
            "executed": False,
            "message": f"Action '{action_name}' is planned but execution is not implemented yet.",
            "plan": plan,
        }

    if action_name == "create_task":
        task = orchestrator.create_task(args.get("title", "New task"), args.get("description", ""))
        set_active_task(session_id, task["id"], user_id=user_id, channel=channel)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "create_bug":
        bug = orchestrator.create_bug(
            title=args.get("title", "New bug"),
            description=args.get("description", ""),
            raw_input=args.get("raw"),
            priority=args.get("priority", "medium"),
            severity=args.get("severity", "unknown"),
        )
        set_active_task(session_id, bug["id"], user_id=user_id, channel=channel)
        return {"executed": True, "action": action_name, "result": bug}
    if action_name == "list_tasks":
        return {"executed": True, "action": action_name, "result": orchestrator.list_tasks()}
    if action_name == "show_task":
        task = orchestrator.get_task(args.get("id", ""))
        if task is not None:
            set_active_task(session_id, task["id"], user_id=user_id, channel=channel)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "focus":
        return {
            "executed": True,
            "action": action_name,
            "result": get_focus(session_id=session_id, user_id=user_id, channel=channel),
        }
    if action_name == "clear_focus":
        return {
            "executed": True,
            "action": action_name,
            "result": clear_focus(session_id=session_id, user_id=user_id, channel=channel),
        }
    if action_name == "set_focus_task":
        return {
            "executed": True,
            "action": action_name,
            "result": set_active_task(session_id, args.get("id", ""), user_id=user_id, channel=channel),
        }
    if action_name == "set_focus_release":
        return {
            "executed": True,
            "action": action_name,
            "result": set_active_release(session_id, args.get("id", ""), user_id=user_id, channel=channel),
        }
    if action_name == "set_focus_decision":
        return {
            "executed": True,
            "action": action_name,
            "result": set_active_decision(session_id, args.get("id", ""), user_id=user_id, channel=channel),
        }
    if action_name == "backlog":
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": backlog.sort_backlog(tasks)}
    if action_name == "ready":
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": backlog.get_ready_tasks(tasks)}
    if action_name == "blocked":
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": backlog.get_blocked_tasks(tasks)}
    if action_name == "next_task":
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": backlog.recommend_next_task(tasks)}
    if action_name == "run_next":
        task, message = orchestrator.run_next_for_task(args.get("id", ""))
        if task is None:
            raise SupervisorError(message)
        return {"executed": True, "action": action_name, "result": {"task": task, "message": message}}
    if action_name == "run_all":
        llm_client = get_llm_client()
        result = orchestrator.run_all_ready_tasks(llm_client=llm_client)
        return {"executed": True, "action": action_name, "result": result}
    if action_name == "attach_repo_context":
        tasks, task = _load_task_for_update(args.get("id", ""))
        task["artifacts"]["repository_context"] = build_repository_context_for_task(task)
        orchestrator.add_history_event(task, "Action executed via Supervisor: attach_repo_context.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "agents":
        return {"executed": True, "action": action_name, "result": orchestrator.list_available_agents()}
    if action_name == "config":
        provider = (os.getenv("LLM_PROVIDER") or "fake").strip().lower()
        model = (os.getenv("OPENAI_MODEL") or "gpt-5.1-mini").strip() or "gpt-5.1-mini"
        has_key = bool((os.getenv("OPENAI_API_KEY") or "").strip())
        return {
            "executed": True,
            "action": action_name,
            "result": {
                "LLM_PROVIDER": provider,
                "OPENAI_MODEL": model,
                "OPENAI_API_KEY_SET": has_key,
            },
        }
    if action_name == "managed_project":
        return {"executed": True, "action": action_name, "result": get_managed_project_info()}
    if action_name == "managed_project_check":
        return {"executed": True, "action": action_name, "result": validate_managed_repo_path()}
    if action_name == "context":
        context = load_project_context()
        files = list_project_context_files()
        rows = []
        for filename in files:
            text = context[filename].strip()
            preview = text.splitlines()[0] if text else "(empty)"
            rows.append({"file": filename, "preview": preview})
        return {"executed": True, "action": action_name, "result": rows}
    if action_name == "project_status":
        return {"executed": True, "action": action_name, "result": get_project_status()}
    if action_name == "task_status":
        return {"executed": True, "action": action_name, "result": get_task_status(args.get("id", ""))}
    if action_name == "release_summary":
        return {"executed": True, "action": action_name, "result": get_release_summary(args.get("id", ""))}
    if action_name == "next_work":
        return {"executed": True, "action": action_name, "result": get_next_work_recommendation()}
    if action_name == "blockers_summary":
        return {"executed": True, "action": action_name, "result": get_blockers_summary()}
    if action_name == "task_notes":
        return {"executed": True, "action": action_name, "result": list_task_notes(args.get("id", ""))}
    if action_name == "summarize_task_discussion":
        return {"executed": True, "action": action_name, "result": summarize_task_discussion(args.get("id", ""))}
    if action_name == "prepare_task_for_dev":
        return {"executed": True, "action": action_name, "result": prepare_task_for_development(args.get("id", ""))}
    if action_name == "advance_task_safely":
        return {
            "executed": True,
            "action": action_name,
            "result": advance_task_safely(args.get("id", ""), target_status=args.get("target_status")),
        }
    if action_name == "add_task_note":
        author = args.get("author", "user")
        return {
            "executed": True,
            "action": action_name,
            "result": add_task_note(args.get("id", ""), args.get("text", ""), author=author),
        }
    if action_name == "repo_scan":
        return {"executed": True, "action": action_name, "result": scan_repository()}
    if action_name == "repo_tree":
        depth = int(args.get("depth", 4))
        return {"executed": True, "action": action_name, "result": list_repository_tree(max_depth=depth)}
    if action_name == "repo_search":
        query = args.get("query", "")
        limit = int(args.get("limit", 20))
        return {"executed": True, "action": action_name, "result": search_repository(query=query, max_results=limit)}
    if action_name == "repo_file":
        path = args.get("path", "")
        max_chars = int(args.get("max_chars", 4000))
        return {
            "executed": True,
            "action": action_name,
            "result": read_repository_file(path=path, max_chars=max_chars),
        }
    if action_name == "list_releases":
        return {"executed": True, "action": action_name, "result": load_releases()}
    if action_name == "show_release":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        if rel is not None:
            set_active_release(session_id, rel["id"], user_id=user_id, channel=channel)
        return {"executed": True, "action": action_name, "result": rel}
    if action_name == "release_readiness":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        if rel is None:
            raise SupervisorError(f"Release not found: {args.get('id')}")
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": calculate_release_readiness(tasks, rel)}
    if action_name == "release_notes":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        if rel is None:
            raise SupervisorError(f"Release not found: {args.get('id')}")
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": generate_release_notes(tasks, rel)}
    if action_name == "release_risks":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        if rel is None:
            raise SupervisorError(f"Release not found: {args.get('id')}")
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": generate_release_risks(tasks, rel)}
    if action_name == "rollback_plan":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        if rel is None:
            raise SupervisorError(f"Release not found: {args.get('id')}")
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": generate_rollback_plan(tasks, rel)}
    if action_name == "create_release":
        rel = create_release(
            name=args.get("name", "Unnamed release"),
            description=args.get("description", ""),
            target_date=args.get("target_date"),
        )
        set_active_release(session_id, rel["id"], user_id=user_id, channel=channel)
        return {"executed": True, "action": action_name, "result": rel}
    if action_name == "add_to_release":
        tasks = orchestrator.load_tasks()
        releases = load_releases()
        add_task_to_release(tasks, releases, args.get("task", ""), args.get("release", ""))
        task = next(item for item in tasks if item.get("id") == args.get("task"))
        orchestrator.add_history_event(task, f"Added task to release {args.get('release')}.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        save_releases(releases)
        return {"executed": True, "action": action_name, "result": {"task": args.get("task"), "release": args.get("release")}}
    if action_name == "remove_from_release":
        tasks = orchestrator.load_tasks()
        releases = load_releases()
        remove_task_from_release(tasks, releases, args.get("task", ""), args.get("release", ""))
        task = next(item for item in tasks if item.get("id") == args.get("task"))
        orchestrator.add_history_event(task, f"Removed task from release {args.get('release')}.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        save_releases(releases)
        return {"executed": True, "action": action_name, "result": {"task": args.get("task"), "release": args.get("release")}}
    if action_name == "set_release_status":
        status = args.get("status", "")
        if status not in ALLOWED_RELEASE_STATUSES:
            raise SupervisorError(f"Release status must be one of: {', '.join(sorted(ALLOWED_RELEASE_STATUSES))}")
        releases = load_releases()
        rel = set_release_status(releases, args.get("id", ""), status)
        save_releases(releases)
        return {"executed": True, "action": action_name, "result": rel}
    if action_name == "list_decisions":
        return {"executed": True, "action": action_name, "result": list_decisions()}
    if action_name == "show_decision":
        decision_id = args.get("id", "")
        item = get_decision_by_id(decision_id)
        if item is None:
            raise SupervisorError(f"Decision not found: {decision_id}")
        set_active_decision(session_id, decision_id, user_id=user_id, channel=channel)
        return {"executed": True, "action": action_name, "result": item}
    if action_name == "task_decisions":
        task = orchestrator.get_task(args.get("id", ""))
        if task is None:
            raise SupervisorError(f"Task not found: {args.get('id')}")
        return {"executed": True, "action": action_name, "result": get_task_related_decisions(task)}
    if action_name == "create_decision":
        metadata = create_decision(
            title=args.get("title", "Untitled decision"),
            context=args.get("context", ""),
            decision=args.get("decision", ""),
            consequences=args.get("consequences", ""),
            status=args.get("status", "accepted"),
            tags=args.get("tags") or [],
            related_tasks=args.get("related_tasks") or [],
        )
        related_tasks = args.get("related_tasks") or []
        if related_tasks:
            tasks = orchestrator.load_tasks()
            for task_id in related_tasks:
                task = link_decision_to_task(tasks, task_id, metadata["id"])
                orchestrator.add_history_event(task, f"Linked decision {metadata['id']} to task.", agent="orchestrator")
            orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": metadata}
    if action_name == "link_decision":
        tasks = orchestrator.load_tasks()
        task = link_decision_to_task(tasks, args.get("id", ""), args.get("decision", ""))
        orchestrator.add_history_event(task, f"Linked decision {args.get('decision')} to task.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "unlink_decision":
        tasks = orchestrator.load_tasks()
        task = unlink_decision_from_task(tasks, args.get("id", ""), args.get("decision", ""))
        orchestrator.add_history_event(task, f"Unlinked decision {args.get('decision')} from task.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "add_dependency":
        tasks, task = _load_task_for_update(args.get("id", ""))
        backlog.add_dependency(tasks, args.get("id", ""), args.get("depends_on", ""))
        orchestrator.add_history_event(
            task,
            f"Added dependency: {args.get('id')} depends on {args.get('depends_on')}.",
            agent="orchestrator",
        )
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "remove_dependency":
        tasks, task = _load_task_for_update(args.get("id", ""))
        backlog.remove_dependency(tasks, args.get("id", ""), args.get("depends_on", ""))
        orchestrator.add_history_event(
            task,
            f"Removed dependency: {args.get('id')} no longer depends on {args.get('depends_on')}.",
            agent="orchestrator",
        )
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "block_task":
        tasks, task = _load_task_for_update(args.get("id", ""))
        blocked_by = args.get("blocked_by", [])
        if isinstance(blocked_by, str):
            blocked_by = [item.strip() for item in blocked_by.split(",") if item.strip()]
        backlog.set_blocker(tasks, args.get("id", ""), blocked_by=blocked_by, blocked_reason=args.get("reason", ""))
        orchestrator.add_history_event(task, f"Task blocked by {blocked_by}: {args.get('reason', '')}", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "unblock_task":
        tasks, task = _load_task_for_update(args.get("id", ""))
        backlog.clear_blocker(tasks, args.get("id", ""))
        orchestrator.add_history_event(task, "Task unblocked.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "approve_patch":
        tasks, task = _load_task_for_update(args.get("id", ""))
        approve_patch(task)
        orchestrator.add_history_event(task, "Patch proposal approved.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task.get("artifacts", {}).get("patch_proposal")}
    if action_name == "apply_patch":
        tasks, task = _load_task_for_update(args.get("id", ""))
        result = apply_patch_proposal(task, repo_root=None, force=bool(args.get("force", False)))
        if not result.get("errors"):
            orchestrator.add_history_event(task, "Patch proposal applied.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": result}
    if action_name == "run_command":
        command = args.get("command", "")
        timeout = int(args.get("timeout", 30))
        if not is_command_allowed(command):
            raise SupervisorError(f"Command is not allowed: {command}")
        result = run_safe_command(command, cwd=None, timeout_seconds=timeout)
        task_id = args.get("id")
        if task_id:
            tasks, task = _load_task_for_update(task_id)
            result["source"] = "manual"
            task["artifacts"]["command_results"].append(result)
            orchestrator.add_history_event(task, f"Executed command: {command}", agent="orchestrator")
            orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": result}
    if action_name == "run_plan_commands":
        task_id = args.get("id", "")
        timeout = int(args.get("timeout", 30))
        tasks, task = _load_task_for_update(task_id)
        commands = task["artifacts"]["implementation_plan"].get("commands_to_run", [])
        executed = []
        skipped = []
        for command in commands:
            if not isinstance(command, str) or not is_command_allowed(command):
                skipped.append(command)
                continue
            result = run_safe_command(command, cwd=None, timeout_seconds=timeout)
            result["source"] = "implementation_plan"
            task["artifacts"]["command_results"].append(result)
            executed.append(command)
        orchestrator.add_history_event(
            task,
            f"Executed implementation plan commands. executed={len(executed)} skipped={len(skipped)}",
            agent="orchestrator",
        )
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": {"executed": executed, "skipped": skipped}}

    raise SupervisorError(f"Action execution not implemented: {action_name}")
