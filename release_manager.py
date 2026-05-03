from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import backlog
import storage

RELEASES_DIR = Path("releases")
RELEASES_PATH = RELEASES_DIR / "releases.json"
ALLOWED_RELEASE_STATUSES = {"planned", "in_progress", "ready", "released", "cancelled"}


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_storage() -> None:
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    storage.JSON_COLLECTION_PATHS["releases"] = RELEASES_PATH
    storage.init_storage()


def load_releases() -> list[dict]:
    _ensure_storage()
    try:
        data = storage.load_collection("releases")
    except storage.StorageError as exc:
        raise ValueError(str(exc)) from exc
    for item in data:
        validate_release(item)
    return data


def save_releases(releases: list[dict]) -> None:
    _ensure_storage()
    for item in releases:
        validate_release(item)
    try:
        storage.save_collection("releases", releases)
    except storage.StorageError as exc:
        raise ValueError(str(exc)) from exc


def validate_release(release: dict) -> None:
    if not isinstance(release, dict):
        raise ValueError("Release must be an object.")
    required = [
        "id",
        "name",
        "description",
        "status",
        "created_at",
        "target_date",
        "tasks",
        "notes",
        "risks",
        "rollback_plan",
        "history",
    ]
    for field in required:
        if field not in release:
            raise ValueError(f"Release missing required field: {field}")
    if release["status"] not in ALLOWED_RELEASE_STATUSES:
        raise ValueError(
            f"Release {release.get('id')} has invalid status: {release['status']}"
        )
    if not isinstance(release["tasks"], list):
        raise ValueError(f"Release {release.get('id')} tasks must be a list.")
    if not isinstance(release["risks"], list):
        raise ValueError(f"Release {release.get('id')} risks must be a list.")
    if not isinstance(release["history"], list):
        raise ValueError(f"Release {release.get('id')} history must be a list.")


def get_release_by_id(releases: list[dict], release_id: str) -> Optional[dict]:
    for item in releases:
        if item.get("id") == release_id:
            return item
    return None


def get_next_release_id(releases: list[dict]) -> str:
    max_num = 0
    for item in releases:
        release_id = str(item.get("id", ""))
        if release_id.startswith("REL-"):
            try:
                max_num = max(max_num, int(release_id.split("-", 1)[1]))
            except ValueError:
                continue
    return f"REL-{max_num + 1:03d}"


def create_release(name: str, description: str = "", target_date: str | None = None) -> dict:
    releases = load_releases()
    release = {
        "id": get_next_release_id(releases),
        "name": name,
        "description": description,
        "status": "planned",
        "created_at": _now_iso_utc(),
        "target_date": target_date,
        "tasks": [],
        "notes": "",
        "risks": [],
        "rollback_plan": "",
        "history": [],
    }
    releases.append(release)
    save_releases(releases)
    return release


def add_task_to_release(tasks: list[dict], releases: list[dict], task_id: str, release_id: str) -> dict:
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    release = get_release_by_id(releases, release_id)
    if release is None:
        raise ValueError(f"Release not found: {release_id}")

    if task_id not in release["tasks"]:
        release["tasks"].append(task_id)
    task["release_id"] = release_id
    return release


def remove_task_from_release(tasks: list[dict], releases: list[dict], task_id: str, release_id: str) -> dict:
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    release = get_release_by_id(releases, release_id)
    if release is None:
        raise ValueError(f"Release not found: {release_id}")

    release["tasks"] = [item for item in release["tasks"] if item != task_id]
    if task.get("release_id") == release_id:
        task["release_id"] = None
    return release


def get_release_tasks(tasks: list[dict], release: dict) -> list[dict]:
    ids = set(release.get("tasks", []))
    return [task for task in tasks if task.get("id") in ids]


def get_release_related_decisions(tasks: list[dict], release: dict) -> list[str]:
    seen = set()
    ordered = []
    for task in get_release_tasks(tasks, release):
        for decision_id in task.get("related_decisions", []):
            if decision_id not in seen:
                seen.add(decision_id)
                ordered.append(decision_id)
    return ordered


def calculate_release_readiness(tasks: list[dict], release: dict) -> dict:
    release_tasks = get_release_tasks(tasks, release)
    not_done = []
    blocked = []
    failed_qa = []
    missing_command_results = []
    unapplied_patches = []
    risks = []

    for task in release_tasks:
        task_id = task["id"]
        if task.get("status") != "done":
            not_done.append(task_id)
        if backlog.is_task_blocked(task, tasks):
            blocked.append(task_id)
        verdict = task.get("artifacts", {}).get("qa_verification", {}).get("verdict")
        if verdict in ("failed", "needs_rework"):
            failed_qa.append(task_id)
        if verdict == "unknown":
            risks.append(f"{task_id}: QA verdict is unknown.")
        if not task.get("artifacts", {}).get("command_results"):
            missing_command_results.append(task_id)
            risks.append(f"{task_id}: Missing command execution evidence.")
        patch = task.get("artifacts", {}).get("patch_proposal", {})
        if patch.get("approved") is True and patch.get("applied") is False:
            unapplied_patches.append(task_id)
            risks.append(f"{task_id}: Approved patch is not applied.")

    ready = (
        len(release_tasks) > 0
        and not not_done
        and not blocked
        and not failed_qa
        and not unapplied_patches
    )
    summary = "Release is ready." if ready else "Release is not ready."
    return {
        "release_id": release["id"],
        "ready": ready,
        "summary": summary,
        "total_tasks": len(release_tasks),
        "done_tasks": len(release_tasks) - len(not_done),
        "not_done_tasks": not_done,
        "blocked_tasks": blocked,
        "failed_qa_tasks": failed_qa,
        "missing_command_results": missing_command_results,
        "unapplied_patches": unapplied_patches,
        "risks": risks,
    }


def generate_release_notes(tasks: list[dict], release: dict) -> str:
    release_tasks = get_release_tasks(tasks, release)
    features = [t for t in release_tasks if t.get("type") == "feature"]
    bugs = [t for t in release_tasks if t.get("type") == "bug"]
    other = [t for t in release_tasks if t.get("type") not in ("feature", "bug")]

    lines = [
        f"# Release Notes: {release['id']} {release.get('name', '')}".strip(),
        "",
        f"Status: {release.get('status')}",
        "",
        "## Features",
        "",
    ]

    def append_tasks(items: list[dict]):
        if not items:
            lines.append("- (none)")
            return
        for task in items:
            summary = task.get("artifacts", {}).get("implementation_plan", {}).get("summary") or ""
            decisions = ", ".join(task.get("related_decisions", [])) or "none"
            command_count = len(task.get("artifacts", {}).get("command_results", []))
            lines.append(
                f"- {task['id']} {task['title']} (status={task['status']})"
                f" | summary={summary or 'n/a'}"
                f" | decisions={decisions}"
                f" | command_results={command_count}"
            )

    append_tasks(features)
    lines.extend(["", "## Bugs", ""])
    append_tasks(bugs)
    lines.extend(["", "## Other", ""])
    append_tasks(other)
    lines.append("")
    return "\n".join(lines)


def generate_release_risks(tasks: list[dict], release: dict) -> list[str]:
    release_tasks = get_release_tasks(tasks, release)
    risks = []
    for task in release_tasks:
        task_id = task["id"]
        for item in task.get("artifacts", {}).get("implementation_plan", {}).get("risks", []):
            risks.append(f"{task_id}: {item}")
        for item in task.get("artifacts", {}).get("qa_verification", {}).get("failed_checks", []):
            risks.append(f"{task_id}: {item}")
        for item in task.get("artifacts", {}).get("qa_verification", {}).get("bugs_found", []):
            risks.append(f"{task_id}: {item}")
        if backlog.is_task_blocked(task, tasks):
            risks.append(f"{task_id}: {backlog.get_blocked_reason(task, tasks)}")
        verdict = task.get("artifacts", {}).get("qa_verification", {}).get("verdict")
        if verdict == "unknown":
            risks.append(f"{task_id}: QA verdict is unknown.")
        if not task.get("artifacts", {}).get("command_results"):
            risks.append(f"{task_id}: Missing command execution evidence.")
        patch = task.get("artifacts", {}).get("patch_proposal", {})
        if patch.get("approved") is True and patch.get("applied") is False:
            risks.append(f"{task_id}: Approved patch is not applied.")

    dedup = []
    seen = set()
    for item in risks:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return dedup


def generate_rollback_plan(tasks: list[dict], release: dict) -> str:
    release_tasks = get_release_tasks(tasks, release)
    lines = [f"# Rollback Plan for {release['id']}", ""]
    notes = []
    applied_patch_tasks = []

    for task in release_tasks:
        rollback_notes = task.get("artifacts", {}).get("implementation_plan", {}).get("rollback_notes")
        if isinstance(rollback_notes, str) and rollback_notes.strip():
            notes.append(f"- {task['id']}: {rollback_notes.strip()}")
        patch = task.get("artifacts", {}).get("patch_proposal", {})
        if patch.get("applied") is True:
            applied_patch_tasks.append(task["id"])

    if notes:
        lines.append("## Task-specific rollback notes")
        lines.append("")
        lines.extend(notes)
        lines.append("")

    if applied_patch_tasks:
        lines.append("## Applied patch review")
        lines.append("")
        lines.append(f"- Applied patches detected for tasks: {', '.join(applied_patch_tasks)}")
        lines.append("- Revert affected files to last known good state if verification fails.")
        lines.append("")

    if not notes and not applied_patch_tasks:
        lines.append("## Generic rollback guidance")
        lines.append("")
        lines.append("- Identify tasks included in the release.")
        lines.append("- Revert recent file changes related to those tasks.")
        lines.append("- Re-run validation commands and QA checks.")
        lines.append("")

    return "\n".join(lines)


def set_release_status(releases: list[dict], release_id: str, status: str) -> dict:
    if status not in ALLOWED_RELEASE_STATUSES:
        raise ValueError(f"Release status must be one of: {', '.join(sorted(ALLOWED_RELEASE_STATUSES))}")
    release = get_release_by_id(releases, release_id)
    if release is None:
        raise ValueError(f"Release not found: {release_id}")
    release["status"] = status
    release.setdefault("history", []).append(
        {
            "timestamp": _now_iso_utc(),
            "message": f"Release status changed to {status}.",
        }
    )
    return release
