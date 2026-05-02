import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent_runner import AGENT_FILES, AGENTS_DIR, run_agent
from llm_client import BaseLLMClient, get_llm_client

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
    "changed_files",
    "developer_notes",
    "test_cases",
    "edge_cases",
    "bugs",
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

TRANSITIONS = {
    "idea": {"from": "idea", "to": "refined", "agent": "analyst"},
    "refined": {"from": "refined", "to": "ready_for_dev", "agent": "architect"},
    "ready_for_dev": {"from": "ready_for_dev", "to": "in_progress", "agent": "developer"},
    "in_progress": {"from": "in_progress", "to": "review", "agent": "qa"},
    "review": {"from": "review", "to": "done", "agent": "orchestrator"},
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

    required_top_level = ["id", "type", "title", "description", "status", "priority", "artifacts", "history"]
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
    artifacts = task.setdefault("artifacts", {})

    defaults = {
        "analysis": None,
        "acceptance_criteria": [],
        "architecture": None,
        "technical_risks": [],
        "implementation_guidance": None,
        "implementation": None,
        "implementation_plan": _default_implementation_plan(),
        "changed_files": [],
        "developer_notes": None,
        "test_cases": [],
        "edge_cases": [],
        "bugs": [],
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

    if task["type"] == "bug":
        task.setdefault("severity", "unknown")
        artifacts["bug_report"] = _normalize_bug_report(artifacts.get("bug_report"))

    task.setdefault("history", [])
    return task


def load_tasks() -> List[dict]:
    if not TASKS_PATH.exists():
        return []

    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {TASKS_PATH.as_posix()}: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"{TASKS_PATH.as_posix()} must contain a JSON array of tasks.")

    normalized = [_normalize_task_schema(task) for task in data]
    for task in normalized:
        validate_task(task)

    return normalized


def save_tasks(tasks: List[dict]) -> None:
    normalized = [_normalize_task_schema(task) for task in tasks]
    for task in normalized:
        validate_task(task)

    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


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

    current_status = task["status"]
    if current_status == "done":
        return task, "Task is already done."

    transition = get_next_transition(current_status)
    if transition is None:
        return task, "Task is already done."

    previous_status = transition["from"]
    next_status = transition["to"]
    agent = transition["agent"]

    if agent == "orchestrator":
        message = "Review approved; task completed."
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
            "artifacts": {},
            "history": [],
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
            "raw_input": raw_input or "",
            "artifacts": {
                "bug_report": _normalize_bug_report(),
            },
            "history": [],
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
