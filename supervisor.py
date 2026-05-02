from __future__ import annotations

import json
from pathlib import Path

import backlog
import orchestrator
from decision_log import list_decisions
from llm_client import LLMClientError, get_llm_client
from project_context_loader import load_project_context_text
from release_manager import calculate_release_readiness, get_release_by_id, load_releases

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
}

RISKY_ACTIONS = {"apply_patch", "run_command", "run_plan_commands", "run_all", "set_release_status"}

IMPLEMENTED_EXECUTION_ACTIONS = {
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
    "list_decisions",
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


def build_supervisor_payload(user_text: str) -> dict:
    return {
        "agent_name": "supervisor",
        "user_text": user_text,
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


def plan_supervisor_action(user_text: str, llm_client=None) -> dict:
    if not SUPERVISOR_PROMPT_PATH.exists():
        raise SupervisorError(f"Supervisor prompt file is missing: {SUPERVISOR_PROMPT_PATH.as_posix()}")
    prompt = SUPERVISOR_PROMPT_PATH.read_text(encoding="utf-8")
    payload = build_supervisor_payload(user_text)
    payload["agent_prompt"] = prompt
    client = llm_client or get_llm_client()
    raw = client.generate(payload)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SupervisorError(f"Supervisor returned invalid JSON: {exc}. Raw output: {raw}") from exc
    validate_supervisor_output(parsed)
    return parsed


def execute_supervisor_action(plan: dict, allow_risky: bool = False) -> dict:
    validate_supervisor_output(plan)
    intent = plan["intent"]
    if intent in {"clarify", "unknown"}:
        return {"executed": False, "message": plan["explanation"], "plan": plan}

    action_name = plan["action"]["name"]
    args = plan["action"]["args"]

    if action_name in RISKY_ACTIONS and not allow_risky:
        raise SupervisorError(f"Action '{action_name}' is risky and requires explicit confirmation.")

    if action_name not in IMPLEMENTED_EXECUTION_ACTIONS:
        return {
            "executed": False,
            "message": f"Action '{action_name}' is planned but execution is not implemented yet.",
            "plan": plan,
        }

    if action_name == "create_task":
        task = orchestrator.create_task(args.get("title", "New task"), args.get("description", ""))
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "create_bug":
        bug = orchestrator.create_bug(
            title=args.get("title", "New bug"),
            description=args.get("description", ""),
            raw_input=args.get("raw"),
            priority=args.get("priority", "medium"),
            severity=args.get("severity", "unknown"),
        )
        return {"executed": True, "action": action_name, "result": bug}
    if action_name == "list_tasks":
        return {"executed": True, "action": action_name, "result": orchestrator.list_tasks()}
    if action_name == "show_task":
        task = orchestrator.get_task(args.get("id", ""))
        return {"executed": True, "action": action_name, "result": task}
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
    if action_name == "attach_repo_context":
        from repo_inspector import build_repository_context_for_task

        tasks = orchestrator.load_tasks()
        task = next((item for item in tasks if item.get("id") == args.get("id")), None)
        if task is None:
            raise SupervisorError(f"Task not found: {args.get('id')}")
        task["artifacts"]["repository_context"] = build_repository_context_for_task(task, repo_root=".")
        orchestrator.add_history_event(task, "Action executed via Supervisor: attach_repo_context.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        return {"executed": True, "action": action_name, "result": task}
    if action_name == "list_releases":
        return {"executed": True, "action": action_name, "result": load_releases()}
    if action_name == "show_release":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        return {"executed": True, "action": action_name, "result": rel}
    if action_name == "release_readiness":
        releases = load_releases()
        rel = get_release_by_id(releases, args.get("id", ""))
        if rel is None:
            raise SupervisorError(f"Release not found: {args.get('id')}")
        tasks = orchestrator.list_tasks()
        return {"executed": True, "action": action_name, "result": calculate_release_readiness(tasks, rel)}
    if action_name == "list_decisions":
        return {"executed": True, "action": action_name, "result": list_decisions()}

    raise SupervisorError(f"Action execution not implemented: {action_name}")
