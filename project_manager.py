from __future__ import annotations

from collections import Counter
from typing import Optional

import backlog
import orchestrator
from release_manager import (
    calculate_release_readiness,
    generate_release_notes,
    generate_release_risks,
    generate_rollback_plan,
    get_release_by_id,
    load_releases,
)
from repo_inspector import build_repository_context_for_task


class ProjectManagerError(Exception):
    pass


def _get_task_or_error(task_id: str) -> dict:
    task = orchestrator.get_task(task_id)
    if task is None:
        raise ProjectManagerError(f"Task not found: {task_id}")
    return task


def _refresh_and_find(tasks: list[dict], task_id: str) -> dict:
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ProjectManagerError(f"Task not found: {task_id}")
    return task


def _artifact_summary(task: dict) -> dict:
    artifacts = task.get("artifacts", {}) if isinstance(task, dict) else {}
    plan = artifacts.get("implementation_plan") or {}
    patch = artifacts.get("patch_proposal") or {}
    command_results = artifacts.get("command_results") or []
    qa = artifacts.get("qa_verification") or {}
    repo_ctx = artifacts.get("repository_context") or {}
    return {
        "has_analysis": bool((artifacts.get("analysis") or "").strip()) if isinstance(artifacts.get("analysis"), str) else bool(artifacts.get("analysis")),
        "has_architecture": bool((artifacts.get("architecture") or "").strip()) if isinstance(artifacts.get("architecture"), str) else bool(artifacts.get("architecture")),
        "has_implementation_plan": bool((plan.get("summary") or "").strip() or plan.get("proposed_changes")),
        "has_patch_proposal": bool((patch.get("summary") or "").strip() or patch.get("files")),
        "has_command_results": bool(command_results),
        "qa_verdict": qa.get("verdict", "unknown"),
        "repository_context_attached": bool(repo_ctx.get("attached")),
    }


def prepare_task_for_development(task_id: str) -> dict:
    tasks = orchestrator.load_tasks()
    task = _refresh_and_find(tasks, task_id)
    started_status = task["status"]
    steps: list[str] = []

    if backlog.is_task_blocked(task, tasks):
        reason = backlog.get_blocked_reason(task, tasks) or task.get("blocked_reason", "")
        return {
            "task_id": task_id,
            "started_status": started_status,
            "final_status": task["status"],
            "steps": steps,
            "blocked": True,
            "message": f"Task is blocked: {reason}",
        }

    while task["status"] in {"idea", "refined"}:
        updated, message = orchestrator.run_next_for_task(task_id)
        if updated is None:
            raise ProjectManagerError(message)
        steps.append(f"{updated['status']}: {message}")
        tasks = orchestrator.load_tasks()
        task = _refresh_and_find(tasks, task_id)

    if task["status"] == "ready_for_dev":
        repo_ctx = task.get("artifacts", {}).get("repository_context") or {}
        if not repo_ctx.get("attached"):
            task["artifacts"]["repository_context"] = build_repository_context_for_task(task)
            orchestrator.add_history_event(task, "Attached repository context in prepare_task_for_development.", agent="orchestrator")
            orchestrator.save_tasks(tasks)
            steps.append("repository_context attached")

    return {
        "task_id": task_id,
        "started_status": started_status,
        "final_status": task["status"],
        "steps": steps,
        "blocked": False,
        "message": "Task prepared for development." if task["status"] == "ready_for_dev" else "Task state unchanged.",
    }


def advance_task_safely(task_id: str, target_status: str | None = None) -> dict:
    tasks = orchestrator.load_tasks()
    task = _refresh_and_find(tasks, task_id)
    started_status = task["status"]
    steps: list[str] = []

    if backlog.is_task_blocked(task, tasks):
        reason = backlog.get_blocked_reason(task, tasks) or task.get("blocked_reason", "")
        return {
            "task_id": task_id,
            "started_status": started_status,
            "final_status": task["status"],
            "blocked": True,
            "requires_confirmation": False,
            "steps": steps,
            "message": f"Task is blocked: {reason}",
        }

    allowed_targets = set(orchestrator.ALLOWED_STATUSES)
    if target_status is not None and target_status not in allowed_targets:
        raise ProjectManagerError(f"Unsupported target status: {target_status}")

    max_steps = 1 if target_status is None else 10
    for _ in range(max_steps):
        if target_status is not None and task["status"] == target_status:
            break
        if task["status"] == "done":
            break
        updated, message = orchestrator.run_next_for_task(task_id)
        if updated is None:
            raise ProjectManagerError(message)
        steps.append(f"{updated['status']}: {message}")
        tasks = orchestrator.load_tasks()
        task = _refresh_and_find(tasks, task_id)
        if target_status is None:
            break

    reached_target = target_status is None or task["status"] == target_status
    return {
        "task_id": task_id,
        "started_status": started_status,
        "final_status": task["status"],
        "target_status": target_status,
        "steps": steps,
        "blocked": False,
        "requires_confirmation": False,
        "reached_target": reached_target,
        "message": "Task advanced safely." if steps else "Task state unchanged.",
    }


def get_next_work_recommendation() -> dict:
    tasks = orchestrator.list_tasks()
    item = backlog.recommend_next_task(tasks)
    if item is None:
        blocked = backlog.get_blocked_tasks(tasks)
        return {
            "next_task": None,
            "message": "No ready tasks. Resolve blockers first.",
            "blocked_count": len(blocked),
        }
    reason = f"Selected by backlog priority/status ordering: {item['status']} / {item['priority']}"
    return {
        "next_task": item,
        "message": reason,
        "blocked_count": len(backlog.get_blocked_tasks(tasks)),
    }


def get_blockers_summary() -> dict:
    tasks = orchestrator.list_tasks()
    blocked = backlog.get_blocked_tasks(tasks)
    rows = []
    for task in blocked:
        rows.append(
            {
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "blocked_reason": backlog.get_blocked_reason(task, tasks) or task.get("blocked_reason", ""),
                "depends_on": task.get("depends_on", []),
                "blocked_by": task.get("blocked_by", []),
            }
        )
    return {
        "blocked_count": len(rows),
        "blocked_tasks": rows,
        "message": "Blocked tasks summary prepared.",
    }


def get_project_status() -> dict:
    tasks = orchestrator.list_tasks()
    releases = load_releases()
    by_status = Counter(task.get("status", "unknown") for task in tasks)
    by_type = Counter(task.get("type", "unknown") for task in tasks)
    ready_tasks = backlog.get_ready_tasks(tasks)
    blocked_tasks = backlog.get_blocked_tasks(tasks)

    ready_releases = 0
    for rel in releases:
        readiness = calculate_release_readiness(tasks, rel)
        if readiness.get("ready"):
            ready_releases += 1

    next_work = get_next_work_recommendation()
    blockers = get_blockers_summary()

    message = (
        f"Задач: {len(tasks)} | ready: {len(ready_tasks)} | blocked: {len(blocked_tasks)} | "
        f"релизов: {len(releases)} (ready: {ready_releases})"
    )

    return {
        "total_tasks": len(tasks),
        "by_status": dict(by_status),
        "by_type": dict(by_type),
        "ready_tasks_count": len(ready_tasks),
        "blocked_tasks_count": len(blocked_tasks),
        "releases_count": len(releases),
        "ready_releases_count": ready_releases,
        "next_recommendation": next_work,
        "top_blockers": blockers.get("blocked_tasks", [])[:5],
        "message": message,
    }


def get_task_status(task_id: str) -> dict:
    task = _get_task_or_error(task_id)
    tasks = orchestrator.list_tasks()
    history = task.get("history", [])[-5:]
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "type": task.get("type"),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "blocked": backlog.is_task_blocked(task, tasks),
        "blocked_reason": backlog.get_blocked_reason(task, tasks),
        "depends_on": task.get("depends_on", []),
        "release_id": task.get("release_id"),
        "related_decisions": task.get("related_decisions", []),
        "latest_history": history,
        "artifact_summary": _artifact_summary(task),
    }


def get_release_summary(release_id: str) -> dict:
    releases = load_releases()
    release = get_release_by_id(releases, release_id)
    if release is None:
        raise ProjectManagerError(f"Release not found: {release_id}")

    tasks = orchestrator.list_tasks()
    readiness = calculate_release_readiness(tasks, release)
    notes = generate_release_notes(tasks, release)
    risks = generate_release_risks(tasks, release)
    rollback = generate_rollback_plan(tasks, release)

    return {
        "release": release,
        "readiness": readiness,
        "notes_preview": "\n".join(notes.splitlines()[:12]),
        "risks": risks,
        "rollback_preview": "\n".join(rollback.splitlines()[:12]),
    }


def add_task_note(task_id: str, note: str, author: str = "user") -> dict:
    text = (note or "").strip()
    if not text:
        raise ProjectManagerError("Note text must be non-empty.")

    tasks = orchestrator.load_tasks()
    task = _refresh_and_find(tasks, task_id)
    record = {
        "timestamp": orchestrator.now_iso_utc(),
        "author": str(author or "user"),
        "text": text,
    }
    task.setdefault("notes", []).append(record)
    orchestrator.add_history_event(task, "Added note to task.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    return record


def list_task_notes(task_id: str) -> list[dict]:
    task = _get_task_or_error(task_id)
    notes = task.get("notes", [])
    if not isinstance(notes, list):
        return []
    return notes


def summarize_task_discussion(task_id: str) -> dict:
    notes = list_task_notes(task_id)
    latest = notes[-1] if notes else None
    authors = sorted({item.get("author", "") for item in notes if isinstance(item, dict)})
    unresolved = [item for item in notes if isinstance(item, dict) and "?" in str(item.get("text", ""))]
    return {
        "task_id": task_id,
        "notes_count": len(notes),
        "authors": authors,
        "latest_note": latest,
        "unresolved_questions_count": len(unresolved),
        "message": "Discussion summary generated.",
    }
