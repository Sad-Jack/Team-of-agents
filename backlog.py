from __future__ import annotations

from typing import Optional

PRIORITY_ORDER = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "unknown": 4,
}


def get_task_by_id(tasks: list[dict], task_id: str) -> Optional[dict]:
    for task in tasks:
        if task.get("id") == task_id:
            return task
    return None


def is_task_done(tasks: list[dict], task_id: str) -> bool:
    task = get_task_by_id(tasks, task_id)
    return bool(task and task.get("status") == "done")


def get_unresolved_dependencies(task: dict, tasks: list[dict]) -> list[str]:
    unresolved = []
    for dependency in task.get("depends_on", []):
        if not isinstance(dependency, str):
            continue
        if not is_task_done(tasks, dependency):
            unresolved.append(dependency)
    return unresolved


def is_task_blocked(task: dict, tasks: list[dict]) -> bool:
    if task.get("status") == "done":
        return False
    if task.get("blocked_by"):
        return True
    return len(get_unresolved_dependencies(task, tasks)) > 0


def get_blocked_reason(task: dict, tasks: list[dict]) -> str:
    blocked_by = task.get("blocked_by", [])
    unresolved = get_unresolved_dependencies(task, tasks)
    reasons = []
    if blocked_by:
        blocker_reason = (task.get("blocked_reason") or "").strip()
        if blocker_reason:
            reasons.append(blocker_reason)
        else:
            reasons.append(f"Blocked by: {', '.join(str(x) for x in blocked_by)}")
    if unresolved:
        reasons.append(f"Unresolved dependencies: {', '.join(unresolved)}")
    return "; ".join(reasons) if reasons else ""


def get_ready_tasks(tasks: list[dict]) -> list[dict]:
    ready = [task for task in tasks if task.get("status") != "done" and not is_task_blocked(task, tasks)]
    return sort_backlog(ready)


def get_blocked_tasks(tasks: list[dict]) -> list[dict]:
    blocked = [task for task in tasks if task.get("status") != "done" and is_task_blocked(task, tasks)]
    return sort_backlog(blocked)


def _priority_rank(task: dict) -> int:
    priority = str(task.get("priority", "unknown")).strip().lower()
    return PRIORITY_ORDER.get(priority, PRIORITY_ORDER["unknown"])


def _type_rank(task: dict) -> int:
    return 0 if task.get("type") == "bug" else 1


def sort_backlog(tasks: list[dict]) -> list[dict]:
    return sorted(
        tasks,
        key=lambda t: (_priority_rank(t), _type_rank(t), str(t.get("id", ""))),
    )


def recommend_next_task(tasks: list[dict]) -> Optional[dict]:
    ready = get_ready_tasks(tasks)
    return ready[0] if ready else None


def _validate_dependency_targets(tasks: list[dict], task_id: str, depends_on: list[str]) -> None:
    if task_id in depends_on:
        raise ValueError(f"Task {task_id} cannot depend on itself.")
    for dependency_id in depends_on:
        if get_task_by_id(tasks, dependency_id) is None:
            raise ValueError(f"Dependency task not found: {dependency_id}")


def set_dependencies(tasks: list[dict], task_id: str, depends_on: list[str]) -> dict:
    task = get_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    _validate_dependency_targets(tasks, task_id, depends_on)
    task["depends_on"] = list(dict.fromkeys(depends_on))
    return task


def add_dependency(tasks: list[dict], task_id: str, dependency_id: str) -> dict:
    task = get_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    depends_on = list(task.get("depends_on", []))
    if dependency_id not in depends_on:
        depends_on.append(dependency_id)
    return set_dependencies(tasks, task_id, depends_on)


def remove_dependency(tasks: list[dict], task_id: str, dependency_id: str) -> dict:
    task = get_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    depends_on = [item for item in task.get("depends_on", []) if item != dependency_id]
    return set_dependencies(tasks, task_id, depends_on)


def set_blocker(tasks: list[dict], task_id: str, blocked_by: list[str], blocked_reason: str) -> dict:
    task = get_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    task["blocked_by"] = list(dict.fromkeys(blocked_by))
    task["blocked_reason"] = blocked_reason or ""
    return task


def clear_blocker(tasks: list[dict], task_id: str) -> dict:
    task = get_task_by_id(tasks, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    task["blocked_by"] = []
    task["blocked_reason"] = ""
    return task
