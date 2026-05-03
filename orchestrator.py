from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import backlog
from agent_runner import AGENT_FILES, AGENTS_DIR, run_agent
from llm_client import BaseLLMClient, get_llm_client
import storage

TASKS_PATH = Path("tasks") / "tasks.json"

ALLOWED_TYPES = ["feature", "bug"]
ALLOWED_STATUSES = [
    "idea",
    "refined",
    "ready_for_dev",
    "in_progress",
    "review",
    "done",
]

REQUIRED_ARTIFACT_FIELDS = [
    "analysis",
    "acceptance_criteria",
    "architecture",
    "technical_risks",
    "implementation_guidance",
    "implementation",
    "implementation_plan",
    "patch_proposal",
    "changed_files",
    "developer_notes",
    "test_cases",
    "edge_cases",
    "bugs",
    "qa_verification",
    "command_results",
    "repository_context",
]

REQUIRED_BUG_REPORT_FIELDS = [
    "summary",
    "environment",
    "steps_to_reproduce",
    "actual_result",
    "expected_result",
    "logs",
    "attachments",
    "suspected_area",
    "impact",
]

ALLOWED_CHANGE_TYPES = ["create", "modify", "delete"]
ALLOWED_QA_VERDICTS = ["unknown", "passed", "failed", "needs_rework"]
ALLOWED_QA_NEXT_STATUSES = ["done", "ready_for_dev", "in_progress", "review"]

TRANSITIONS = {
    "idea": {"from": "idea", "to": "refined", "agent": "analyst"},
    "refined": {"from": "refined", "to": "ready_for_dev", "agent": "architect"},
    "ready_for_dev": {"from": "ready_for_dev", "to": "in_progress", "agent": "developer"},
    "in_progress": {"from": "in_progress", "to": "review", "agent": "qa"},
}


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_agent_prompt_path(agent_name: str) -> Path:
    filename = AGENT_FILES.get(agent_name)
    if filename is None:
        raise ValueError(f"Unknown agent: {agent_name}")
    return AGENTS_DIR / filename


def load_agent_prompt(agent_name: str) -> str:
    prompt_path = get_agent_prompt_path(agent_name)
    if not prompt_path.exists():
        raise ValueError(f"Agent prompt file is missing: {prompt_path.as_posix()}")
    return prompt_path.read_text(encoding="utf-8")


def list_available_agents() -> List[Dict[str, str]]:
    items = []
    for agent_name in ("analyst", "architect", "developer", "qa", "bug_intake"):
        prompt_path = get_agent_prompt_path(agent_name)
        items.append(
            {
                "agent": agent_name,
                "prompt_source": prompt_path.as_posix(),
                "exists": str(prompt_path.exists()).lower(),
            }
        )
    return items


def _validate_bug_report(task_id: str, bug_report: dict) -> None:
    if not isinstance(bug_report, dict):
        raise ValueError(f"Task {task_id} artifacts.bug_report must be an object.")

    for field in REQUIRED_BUG_REPORT_FIELDS:
        if field not in bug_report:
            raise ValueError(f"Task {task_id} bug_report missing field: {field}")

    for string_field in ("summary", "environment", "actual_result", "expected_result", "suspected_area", "impact"):
        if not isinstance(bug_report[string_field], str):
            raise ValueError(f"Task {task_id} bug_report.{string_field} must be a string.")

    for list_field in ("steps_to_reproduce", "logs", "attachments"):
        if not isinstance(bug_report[list_field], list):
            raise ValueError(f"Task {task_id} bug_report.{list_field} must be a list.")


def _default_implementation_plan() -> dict:
    return {
        "summary": "",
        "files_to_create": [],
        "files_to_modify": [],
        "proposed_changes": [],
        "commands_to_run": [],
        "tests_to_add": [],
        "risks": [],
        "rollback_notes": "",
    }


def _default_patch_proposal() -> dict:
    return {
        "summary": "",
        "files": [],
        "unified_diff": "",
        "requires_approval": True,
        "approved": False,
        "applied": False,
        "applied_at": None,
    }


def _default_qa_verification() -> dict:
    return {
        "verdict": "unknown",
        "summary": "",
        "checked_items": [],
        "failed_checks": [],
        "bugs_found": [],
        "recommended_next_status": "review",
    }


def _default_repository_context() -> dict:
    return {
        "attached": False,
        "scanned_at": None,
        "repo_root": ".",
        "summary": {
            "total_files_indexed": 0,
            "interesting_directories": [],
            "ignored_paths": [],
        },
        "relevant_files": [],
        "search_hits": [],
    }


def _validate_repository_context(task_id: str, context: dict) -> None:
    if not isinstance(context, dict):
        raise ValueError(f"Task {task_id} artifacts.repository_context must be an object.")
    for field in ("attached", "scanned_at", "repo_root", "summary", "relevant_files", "search_hits"):
        if field not in context:
            raise ValueError(f"Task {task_id} repository_context missing field: {field}")

    if not isinstance(context["attached"], bool):
        raise ValueError(f"Task {task_id} repository_context.attached must be a boolean.")
    if context["scanned_at"] is not None and not isinstance(context["scanned_at"], str):
        raise ValueError(f"Task {task_id} repository_context.scanned_at must be a string or null.")
    if not isinstance(context["repo_root"], str):
        raise ValueError(f"Task {task_id} repository_context.repo_root must be a string.")
    if not isinstance(context["relevant_files"], list):
        raise ValueError(f"Task {task_id} repository_context.relevant_files must be a list.")
    if not isinstance(context["search_hits"], list):
        raise ValueError(f"Task {task_id} repository_context.search_hits must be a list.")

    summary = context["summary"]
    if not isinstance(summary, dict):
        raise ValueError(f"Task {task_id} repository_context.summary must be an object.")
    for field in ("total_files_indexed", "interesting_directories", "ignored_paths"):
        if field not in summary:
            raise ValueError(f"Task {task_id} repository_context.summary missing field: {field}")
    if not isinstance(summary["total_files_indexed"], int):
        raise ValueError(f"Task {task_id} repository_context.summary.total_files_indexed must be an int.")
    if not isinstance(summary["interesting_directories"], list):
        raise ValueError(f"Task {task_id} repository_context.summary.interesting_directories must be a list.")
    if not isinstance(summary["ignored_paths"], list):
        raise ValueError(f"Task {task_id} repository_context.summary.ignored_paths must be a list.")

    for idx, item in enumerate(context["relevant_files"]):
        if not isinstance(item, dict):
            raise ValueError(f"Task {task_id} repository_context.relevant_files[{idx}] must be an object.")
        for field in ("path", "reason", "size_bytes", "preview"):
            if field not in item:
                raise ValueError(f"Task {task_id} repository_context.relevant_files[{idx}] missing field: {field}")
        if not isinstance(item["path"], str):
            raise ValueError(f"Task {task_id} repository_context.relevant_files[{idx}].path must be a string.")
        if not isinstance(item["reason"], str):
            raise ValueError(f"Task {task_id} repository_context.relevant_files[{idx}].reason must be a string.")
        if not isinstance(item["size_bytes"], int):
            raise ValueError(f"Task {task_id} repository_context.relevant_files[{idx}].size_bytes must be an int.")
        if not isinstance(item["preview"], str):
            raise ValueError(f"Task {task_id} repository_context.relevant_files[{idx}].preview must be a string.")

    for idx, hit in enumerate(context["search_hits"]):
        if not isinstance(hit, dict):
            raise ValueError(f"Task {task_id} repository_context.search_hits[{idx}] must be an object.")
        for field in ("path", "line_number", "line"):
            if field not in hit:
                raise ValueError(f"Task {task_id} repository_context.search_hits[{idx}] missing field: {field}")
        if not isinstance(hit["path"], str):
            raise ValueError(f"Task {task_id} repository_context.search_hits[{idx}].path must be a string.")
        if not isinstance(hit["line_number"], int):
            raise ValueError(f"Task {task_id} repository_context.search_hits[{idx}].line_number must be an int.")
        if not isinstance(hit["line"], str):
            raise ValueError(f"Task {task_id} repository_context.search_hits[{idx}].line must be a string.")


def _validate_qa_verification(task_id: str, report: dict) -> None:
    if not isinstance(report, dict):
        raise ValueError(f"Task {task_id} artifacts.qa_verification must be an object.")
    required = [
        "verdict",
        "summary",
        "checked_items",
        "failed_checks",
        "bugs_found",
        "recommended_next_status",
    ]
    for field in required:
        if field not in report:
            raise ValueError(f"Task {task_id} qa_verification missing field: {field}")
    if report["verdict"] not in ALLOWED_QA_VERDICTS:
        raise ValueError(
            f"Task {task_id} qa_verification.verdict must be one of: {', '.join(ALLOWED_QA_VERDICTS)}"
        )
    if not isinstance(report["summary"], str):
        raise ValueError(f"Task {task_id} qa_verification.summary must be a string.")
    for list_field in ("checked_items", "failed_checks", "bugs_found"):
        if not isinstance(report[list_field], list):
            raise ValueError(f"Task {task_id} qa_verification.{list_field} must be a list.")
    if report["recommended_next_status"] not in ALLOWED_QA_NEXT_STATUSES:
        raise ValueError(
            f"Task {task_id} qa_verification.recommended_next_status must be one of: {', '.join(ALLOWED_QA_NEXT_STATUSES)}"
        )


def _validate_patch_proposal(task_id: str, proposal: dict) -> None:
    if not isinstance(proposal, dict):
        raise ValueError(f"Task {task_id} artifacts.patch_proposal must be an object.")
    required = [
        "summary",
        "files",
        "unified_diff",
        "requires_approval",
        "approved",
        "applied",
        "applied_at",
    ]
    for field in required:
        if field not in proposal:
            raise ValueError(f"Task {task_id} patch_proposal missing field: {field}")
    if not isinstance(proposal["summary"], str):
        raise ValueError(f"Task {task_id} patch_proposal.summary must be a string.")
    if not isinstance(proposal["files"], list):
        raise ValueError(f"Task {task_id} patch_proposal.files must be a list.")
    if not isinstance(proposal["unified_diff"], str):
        raise ValueError(f"Task {task_id} patch_proposal.unified_diff must be a string.")
    for bool_field in ("requires_approval", "approved", "applied"):
        if not isinstance(proposal[bool_field], bool):
            raise ValueError(f"Task {task_id} patch_proposal.{bool_field} must be a boolean.")
    if proposal["applied_at"] is not None and not isinstance(proposal["applied_at"], str):
        raise ValueError(f"Task {task_id} patch_proposal.applied_at must be a string or null.")

    for idx, item in enumerate(proposal["files"]):
        if not isinstance(item, dict):
            raise ValueError(f"Task {task_id} patch_proposal.files[{idx}] must be an object.")
        for key in ("file_path", "change_type", "reason", "content", "safe_to_apply"):
            if key not in item:
                raise ValueError(f"Task {task_id} patch_proposal.files[{idx}] missing field: {key}")
        if not isinstance(item["file_path"], str):
            raise ValueError(f"Task {task_id} patch_proposal.files[{idx}].file_path must be a string.")
        if item["change_type"] not in ALLOWED_CHANGE_TYPES:
            raise ValueError(
                f"Task {task_id} patch_proposal.files[{idx}].change_type must be one of: {', '.join(ALLOWED_CHANGE_TYPES)}"
            )
        if not isinstance(item["reason"], str):
            raise ValueError(f"Task {task_id} patch_proposal.files[{idx}].reason must be a string.")
        if not isinstance(item["content"], str):
            raise ValueError(f"Task {task_id} patch_proposal.files[{idx}].content must be a string.")
        if not isinstance(item["safe_to_apply"], bool):
            raise ValueError(f"Task {task_id} patch_proposal.files[{idx}].safe_to_apply must be a boolean.")


def _validate_command_results(task_id: str, results: list) -> None:
    if not isinstance(results, list):
        raise ValueError(f"Task {task_id} artifacts.command_results must be a list.")
    required = [
        "command",
        "exit_code",
        "success",
        "stdout",
        "stderr",
        "started_at",
        "finished_at",
        "duration_seconds",
        "source",
        "working_directory",
    ]
    for idx, item in enumerate(results):
        if not isinstance(item, dict):
            raise ValueError(f"Task {task_id} command_results[{idx}] must be an object.")
        for field in required:
            if field not in item:
                raise ValueError(f"Task {task_id} command_results[{idx}] missing field: {field}")
        if not isinstance(item["command"], str):
            raise ValueError(f"Task {task_id} command_results[{idx}].command must be a string.")
        if item["exit_code"] is not None and not isinstance(item["exit_code"], int):
            raise ValueError(f"Task {task_id} command_results[{idx}].exit_code must be int or null.")
        if not isinstance(item["success"], bool):
            raise ValueError(f"Task {task_id} command_results[{idx}].success must be a boolean.")
        for s in ("stdout", "stderr", "started_at", "finished_at", "source", "working_directory"):
            if not isinstance(item[s], str):
                raise ValueError(f"Task {task_id} command_results[{idx}].{s} must be a string.")
        if not isinstance(item["duration_seconds"], (int, float)):
            raise ValueError(f"Task {task_id} command_results[{idx}].duration_seconds must be a number.")


def _validate_implementation_plan(task_id: str, plan: dict) -> None:
    if not isinstance(plan, dict):
        raise ValueError(f"Task {task_id} artifacts.implementation_plan must be an object.")

    required = [
        "summary",
        "files_to_create",
        "files_to_modify",
        "proposed_changes",
        "commands_to_run",
        "tests_to_add",
        "risks",
        "rollback_notes",
    ]
    for field in required:
        if field not in plan:
            raise ValueError(f"Task {task_id} implementation_plan missing field: {field}")

    if not isinstance(plan["summary"], str):
        raise ValueError(f"Task {task_id} implementation_plan.summary must be a string.")
    if not isinstance(plan["rollback_notes"], str):
        raise ValueError(f"Task {task_id} implementation_plan.rollback_notes must be a string.")

    for list_field in (
        "files_to_create",
        "files_to_modify",
        "proposed_changes",
        "commands_to_run",
        "tests_to_add",
        "risks",
    ):
        if not isinstance(plan[list_field], list):
            raise ValueError(f"Task {task_id} implementation_plan.{list_field} must be a list.")

    for idx, change in enumerate(plan["proposed_changes"]):
        if not isinstance(change, dict):
            raise ValueError(
                f"Task {task_id} implementation_plan.proposed_changes[{idx}] must be an object."
            )
        for key in ("file_path", "change_type", "reason", "description", "safe_to_apply"):
            if key not in change:
                raise ValueError(
                    f"Task {task_id} proposed_changes[{idx}] missing field: {key}"
                )
        if not isinstance(change["file_path"], str):
            raise ValueError(f"Task {task_id} proposed_changes[{idx}].file_path must be a string.")
        if change["change_type"] not in ALLOWED_CHANGE_TYPES:
            raise ValueError(
                f"Task {task_id} proposed_changes[{idx}].change_type must be one of: {', '.join(ALLOWED_CHANGE_TYPES)}"
            )
        if not isinstance(change["reason"], str):
            raise ValueError(f"Task {task_id} proposed_changes[{idx}].reason must be a string.")
        if not isinstance(change["description"], str):
            raise ValueError(
                f"Task {task_id} proposed_changes[{idx}].description must be a string."
            )
        if not isinstance(change["safe_to_apply"], bool):
            raise ValueError(
                f"Task {task_id} proposed_changes[{idx}].safe_to_apply must be a boolean."
            )


def validate_task(task: dict) -> None:
    if not isinstance(task, dict):
        raise ValueError("Task must be a JSON object.")

    required_top_level = [
        "id",
        "type",
        "title",
        "description",
        "status",
        "priority",
        "artifacts",
        "history",
        "related_decisions",
        "release_id",
    ]
    for key in required_top_level:
        if key not in task:
            raise ValueError(f"Task is missing required field: {key}")

    if not isinstance(task["id"], str) or not task["id"].strip():
        raise ValueError("Task id must be a non-empty string.")

    if task["type"] not in ALLOWED_TYPES:
        raise ValueError(f"Task {task['id']} has invalid type: {task['type']}")

    if not isinstance(task["title"], str) or not task["title"].strip():
        raise ValueError(f"Task {task.get('id', '<unknown>')} title must be a non-empty string.")

    if not isinstance(task["description"], str):
        raise ValueError(f"Task {task['id']} description must be a string.")

    if task["status"] not in ALLOWED_STATUSES:
        raise ValueError(f"Task {task['id']} has invalid status: {task['status']}")

    if not isinstance(task["priority"], str):
        raise ValueError(f"Task {task['id']} priority must be a string.")

    if not isinstance(task["history"], list):
        raise ValueError(f"Task {task['id']} history must be a list.")
    if "notes" not in task:
        raise ValueError(f"Task {task['id']} is missing required field: notes")
    if not isinstance(task["notes"], list):
        raise ValueError(f"Task {task['id']} notes must be a list.")
    for idx, note in enumerate(task["notes"]):
        if not isinstance(note, dict):
            raise ValueError(f"Task {task['id']} notes[{idx}] must be an object.")
        for key in ("timestamp", "author", "text"):
            if key not in note:
                raise ValueError(f"Task {task['id']} notes[{idx}] missing field: {key}")
            if not isinstance(note[key], str):
                raise ValueError(f"Task {task['id']} notes[{idx}].{key} must be a string.")
    for list_field in ("depends_on", "blocked_by", "tags"):
        if list_field not in task:
            raise ValueError(f"Task {task['id']} is missing required field: {list_field}")
        if not isinstance(task[list_field], list):
            raise ValueError(f"Task {task['id']} {list_field} must be a list.")
    if not isinstance(task.get("blocked_reason"), str):
        raise ValueError(f"Task {task['id']} blocked_reason must be a string.")
    estimate = task.get("estimate")
    if estimate is not None and not isinstance(estimate, (int, float, str)):
        raise ValueError(f"Task {task['id']} estimate must be null, int, float, or string.")
    for dep in task["depends_on"]:
        if not isinstance(dep, str):
            raise ValueError(f"Task {task['id']} depends_on values must be strings.")
        if dep == task["id"]:
            raise ValueError(f"Task {task['id']} cannot depend on itself.")
    if not isinstance(task["related_decisions"], list):
        raise ValueError(f"Task {task['id']} related_decisions must be a list.")
    for decision_id in task["related_decisions"]:
        if not isinstance(decision_id, str):
            raise ValueError(f"Task {task['id']} related_decisions values must be strings.")
    release_id = task.get("release_id")
    if release_id is not None and not isinstance(release_id, str):
        raise ValueError(f"Task {task['id']} release_id must be null or string.")

    artifacts = task["artifacts"]
    if not isinstance(artifacts, dict):
        raise ValueError(f"Task {task['id']} artifacts must be an object.")

    for field in REQUIRED_ARTIFACT_FIELDS:
        if field not in artifacts:
            raise ValueError(f"Task {task['id']} artifacts missing field: {field}")

    for list_field in (
        "acceptance_criteria",
        "technical_risks",
        "changed_files",
        "test_cases",
        "edge_cases",
        "bugs",
    ):
        if not isinstance(artifacts[list_field], list):
            raise ValueError(f"Task {task['id']} artifacts.{list_field} must be a list.")
    _validate_implementation_plan(task["id"], artifacts["implementation_plan"])
    _validate_patch_proposal(task["id"], artifacts["patch_proposal"])
    _validate_qa_verification(task["id"], artifacts["qa_verification"])
    _validate_command_results(task["id"], artifacts["command_results"])
    _validate_repository_context(task["id"], artifacts["repository_context"])

    if task["type"] == "bug":
        if "severity" not in task or not isinstance(task["severity"], str):
            raise ValueError(f"Task {task['id']} severity must exist and be a string for bug tasks.")
        if "bug_report" not in artifacts:
            raise ValueError(f"Task {task['id']} artifacts.bug_report is required for bug tasks.")
        _validate_bug_report(task["id"], artifacts["bug_report"])


def _normalize_bug_report(report: Optional[dict] = None) -> dict:
    data = report if isinstance(report, dict) else {}
    defaults = {
        "summary": "unknown",
        "environment": "unknown",
        "steps_to_reproduce": [],
        "actual_result": "unknown",
        "expected_result": "unknown",
        "logs": [],
        "attachments": [],
        "suspected_area": "unknown",
        "impact": "unknown",
    }
    normalized = {}
    for key, default_value in defaults.items():
        normalized[key] = data.get(key, default_value)
    return normalized


def _normalize_task_schema(task: dict) -> dict:
    task.setdefault("type", "feature")
    task.setdefault("depends_on", [])
    task.setdefault("blocked_by", [])
    task.setdefault("blocked_reason", "")
    task.setdefault("tags", [])
    task.setdefault("estimate", None)
    task.setdefault("related_decisions", [])
    task.setdefault("release_id", None)
    artifacts = task.setdefault("artifacts", {})

    defaults = {
        "analysis": None,
        "acceptance_criteria": [],
        "architecture": None,
        "technical_risks": [],
        "implementation_guidance": None,
        "implementation": None,
        "implementation_plan": _default_implementation_plan(),
        "patch_proposal": _default_patch_proposal(),
        "changed_files": [],
        "developer_notes": None,
        "test_cases": [],
        "edge_cases": [],
        "bugs": [],
        "qa_verification": _default_qa_verification(),
        "command_results": [],
        "repository_context": _default_repository_context(),
    }
    for key, default_value in defaults.items():
        artifacts.setdefault(key, default_value)
    if not isinstance(artifacts.get("implementation_plan"), dict):
        artifacts["implementation_plan"] = _default_implementation_plan()
    else:
        current = artifacts["implementation_plan"]
        merged = _default_implementation_plan()
        for key in merged:
            if key in current:
                merged[key] = current[key]
        artifacts["implementation_plan"] = merged
    if not isinstance(artifacts.get("qa_verification"), dict):
        artifacts["qa_verification"] = _default_qa_verification()
    else:
        current_q = artifacts["qa_verification"]
        merged_q = _default_qa_verification()
        for key in merged_q:
            if key in current_q:
                merged_q[key] = current_q[key]
        artifacts["qa_verification"] = merged_q
    if not isinstance(artifacts.get("patch_proposal"), dict):
        artifacts["patch_proposal"] = _default_patch_proposal()
    else:
        current_p = artifacts["patch_proposal"]
        merged_p = _default_patch_proposal()
        for key in merged_p:
            if key in current_p:
                merged_p[key] = current_p[key]
        artifacts["patch_proposal"] = merged_p
    if not isinstance(artifacts.get("command_results"), list):
        artifacts["command_results"] = []
    if not isinstance(artifacts.get("repository_context"), dict):
        artifacts["repository_context"] = _default_repository_context()
    else:
        current_r = artifacts["repository_context"]
        merged_r = _default_repository_context()
        for key in merged_r:
            if key in current_r:
                merged_r[key] = current_r[key]
        if not isinstance(merged_r.get("summary"), dict):
            merged_r["summary"] = _default_repository_context()["summary"]
        else:
            summary_defaults = _default_repository_context()["summary"]
            summary_current = merged_r["summary"]
            merged_summary = dict(summary_defaults)
            for key in summary_defaults:
                if key in summary_current:
                    merged_summary[key] = summary_current[key]
            merged_r["summary"] = merged_summary
        artifacts["repository_context"] = merged_r

    if task["type"] == "bug":
        task.setdefault("severity", "unknown")
        artifacts["bug_report"] = _normalize_bug_report(artifacts.get("bug_report"))

    task.setdefault("history", [])
    task.setdefault("notes", [])
    return task


def load_tasks() -> List[dict]:
    storage.JSON_COLLECTION_PATHS["tasks"] = TASKS_PATH
    try:
        data = storage.load_collection("tasks")
    except storage.StorageError as exc:
        raise ValueError(str(exc)) from exc

    normalized = [_normalize_task_schema(task) for task in data]
    for task in normalized:
        validate_task(task)

    return normalized


def save_tasks(tasks: List[dict]) -> None:
    normalized = [_normalize_task_schema(task) for task in tasks]
    for task in normalized:
        validate_task(task)
    storage.JSON_COLLECTION_PATHS["tasks"] = TASKS_PATH
    try:
        storage.save_collection("tasks", normalized)
    except storage.StorageError as exc:
        raise ValueError(str(exc)) from exc


def _next_id_with_prefix(tasks: List[dict], prefix: str) -> str:
    max_num = 0
    for task in tasks:
        task_id = str(task.get("id", ""))
        if task_id.startswith(f"{prefix}-"):
            try:
                max_num = max(max_num, int(task_id.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{max_num + 1}"


def _find_task(tasks: List[dict], task_id: str) -> Optional[dict]:
    for task in tasks:
        if task.get("id") == task_id:
            return task
    return None


def _append_history(
    task: dict,
    previous_status,
    new_status,
    agent: str,
    message: str,
    prompt_source: str,
    llm_provider: str,
    context_files_used: Optional[List[str]] = None,
) -> None:
    item = {
        "timestamp": now_iso_utc(),
        "agent": agent,
        "previous_status": previous_status,
        "new_status": new_status,
        "message": message,
        "prompt_source": prompt_source,
        "llm_provider": llm_provider,
    }
    if context_files_used is not None:
        item["context_files_used"] = context_files_used
    task["history"].append(item)


def get_next_transition(status: str) -> Optional[dict]:
    if status == "done":
        return None
    transition = TRANSITIONS.get(status)
    if transition is None:
        raise ValueError(f"Unknown status: {status}")
    return dict(transition)


def _review_gate_transition(task: dict) -> Optional[dict]:
    verdict = task["artifacts"]["qa_verification"]["verdict"]
    if verdict == "passed":
        return {"from": "review", "to": "done", "agent": "orchestrator", "message": "QA verification passed; task completed."}
    if verdict in ("failed", "needs_rework"):
        return {
            "from": "review",
            "to": "ready_for_dev",
            "agent": "orchestrator",
            "message": "QA verification requires rework; task returned to ready_for_dev.",
        }
    return None


def _apply_agent_artifacts(task: dict, agent_result: dict) -> None:
    artifacts = agent_result.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError("Agent result artifacts must be a dictionary.")

    for key, value in artifacts.items():
        if key in task["artifacts"]:
            task["artifacts"][key] = value


def run_next_step(task: dict, llm_client: Optional[BaseLLMClient] = None) -> Tuple[dict, str]:
    _normalize_task_schema(task)
    validate_task(task)
    tasks = load_tasks()

    current_status = task["status"]
    if current_status == "done":
        return task, "Task is already done."
    if backlog.is_task_blocked(task, tasks):
        reason = backlog.get_blocked_reason(task, tasks) or "Task has unresolved blockers."
        return task, f"Task {task['id']} is blocked: {reason}"

    if current_status == "review":
        transition = _review_gate_transition(task)
        if transition is None:
            return task, "QA verification is unknown; task remains in review."
    else:
        transition = get_next_transition(current_status)
    if transition is None:
        return task, "Task is already done."

    previous_status = transition["from"]
    next_status = transition["to"]
    agent = transition["agent"]

    if agent == "orchestrator":
        message = transition.get("message", "Review approved; task completed.")
        prompt_source = "orchestrator"
        llm_provider = "orchestrator"
        context_files_used = None
    else:
        client = llm_client or get_llm_client()
        agent_result = run_agent(agent, task, llm_client=client)
        _apply_agent_artifacts(task, agent_result)
        message = str(agent_result.get("message") or f"{agent} processed task.")
        prompt_source = str(agent_result.get("prompt_source") or get_agent_prompt_path(agent).as_posix())
        llm_provider = getattr(client, "provider_name", "fake")
        context_files_used = agent_result.get("context_files_used")

    task["status"] = next_status
    _append_history(
        task,
        previous_status,
        next_status,
        agent,
        message,
        prompt_source,
        llm_provider,
        context_files_used=context_files_used,
    )
    validate_task(task)
    return task, message


def run_all_ready_tasks(llm_client: Optional[BaseLLMClient] = None) -> List[dict]:
    tasks = load_tasks()
    processed = []

    for task in tasks:
        if task["status"] == "done":
            continue

        _, message = run_next_step(task, llm_client=llm_client)
        processed.append(
            {
                "id": task["id"],
                "type": task["type"],
                "status": task["status"],
                "message": message,
            }
        )

    save_tasks(tasks)
    return processed


def create_task(title: str, description: str) -> dict:
    tasks = load_tasks()
    new_task = _normalize_task_schema(
        {
            "id": _next_id_with_prefix(tasks, "TASK"),
            "type": "feature",
            "title": title,
            "description": description,
            "status": "idea",
            "priority": "medium",
            "depends_on": [],
            "blocked_by": [],
            "blocked_reason": "",
            "tags": [],
            "estimate": None,
            "artifacts": {},
            "history": [],
            "notes": [],
        }
    )

    validate_task(new_task)
    tasks.append(new_task)
    save_tasks(tasks)
    return new_task


def create_bug(
    title: str,
    description: str,
    raw_input: Optional[str] = None,
    priority: str = "medium",
    severity: str = "unknown",
    llm_client: Optional[BaseLLMClient] = None,
) -> dict:
    tasks = load_tasks()
    client = llm_client or get_llm_client()

    new_bug = _normalize_task_schema(
        {
            "id": _next_id_with_prefix(tasks, "BUG"),
            "type": "bug",
            "title": title,
            "description": description,
            "status": "idea",
            "priority": priority,
            "severity": severity,
            "depends_on": [],
            "blocked_by": [],
            "blocked_reason": "",
            "tags": [],
            "estimate": None,
            "raw_input": raw_input or "",
        "artifacts": {
                "bug_report": _normalize_bug_report(),
        },
        "history": [],
        "notes": [],
    }
    )

    agent_result = run_agent("bug_intake", new_bug, llm_client=client)
    _apply_agent_artifacts(new_bug, agent_result)

    if isinstance(agent_result.get("severity"), str) and agent_result["severity"].strip():
        new_bug["severity"] = agent_result["severity"].strip()
    if isinstance(agent_result.get("priority"), str) and agent_result["priority"].strip():
        new_bug["priority"] = agent_result["priority"].strip()

    _append_history(
        new_bug,
        None,
        "idea",
        "bug_intake",
        str(agent_result.get("message") or "Bug Intake created a structured bug report."),
        str(agent_result.get("prompt_source") or "agents/bug_intake.md"),
        getattr(client, "provider_name", "fake"),
        context_files_used=agent_result.get("context_files_used"),
    )

    validate_task(new_bug)
    tasks.append(new_bug)
    save_tasks(tasks)
    return new_bug


def list_tasks() -> List[dict]:
    return load_tasks()


def get_task(task_id: str) -> Optional[dict]:
    tasks = load_tasks()
    return _find_task(tasks, task_id)


def get_task_implementation_plan(task_id: str) -> Optional[dict]:
    task = get_task(task_id)
    if task is None:
        return None
    return task["artifacts"].get("implementation_plan")


def get_task_patch_proposal(task_id: str) -> Optional[dict]:
    task = get_task(task_id)
    if task is None:
        return None
    return task["artifacts"].get("patch_proposal")


def add_history_event(task: dict, message: str, agent: str = "orchestrator") -> None:
    _append_history(
        task,
        task["status"],
        task["status"],
        agent,
        message,
        None,
        None,
        context_files_used=None,
    )


def run_next_for_task(task_id: str, llm_client: Optional[BaseLLMClient] = None) -> Tuple[Optional[dict], str]:
    tasks = load_tasks()
    task = _find_task(tasks, task_id)
    if task is None:
        return None, f"Task not found: {task_id}"

    _, message = run_next_step(task, llm_client=llm_client)
    save_tasks(tasks)
    return task, message


def validate_all_tasks() -> int:
    tasks = load_tasks()
    for task in tasks:
        validate_task(task)
    return len(tasks)
