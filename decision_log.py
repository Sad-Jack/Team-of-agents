from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import storage

ALLOWED_DECISION_STATUSES = {"proposed", "accepted", "superseded", "rejected"}
DECISIONS_DIR = Path("decisions")
DECISION_INDEX_PATH = DECISIONS_DIR / "index.json"


def _ensure_decisions_storage() -> None:
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    storage.JSON_COLLECTION_PATHS["decisions_index"] = DECISION_INDEX_PATH
    storage.init_storage()


def load_decision_index() -> list[dict]:
    _ensure_decisions_storage()
    try:
        data = storage.load_collection("decisions_index")
    except storage.StorageError as exc:
        raise ValueError(str(exc)) from exc
    return data


def save_decision_index(index: list[dict]) -> None:
    _ensure_decisions_storage()
    try:
        storage.save_collection("decisions_index", index)
    except storage.StorageError as exc:
        raise ValueError(str(exc)) from exc


def list_decisions() -> list[dict]:
    return load_decision_index()


def get_decision_by_id(decision_id: str) -> Optional[dict]:
    for item in load_decision_index():
        if item.get("id") == decision_id:
            return item
    return None


def get_next_decision_id() -> str:
    max_num = 0
    for item in load_decision_index():
        current = str(item.get("id", ""))
        if current.startswith("ADR-"):
            try:
                max_num = max(max_num, int(current.split("-", 1)[1]))
            except ValueError:
                continue
    return f"ADR-{max_num + 1:03d}"


def _decision_file_path(decision_id: str) -> Path:
    return DECISIONS_DIR / f"{decision_id}.md"


def create_decision(
    title: str,
    context: str,
    decision: str,
    consequences: str,
    status: str = "accepted",
    tags: list[str] | None = None,
    related_tasks: list[str] | None = None,
) -> dict:
    if status not in ALLOWED_DECISION_STATUSES:
        raise ValueError(f"Decision status must be one of: {', '.join(sorted(ALLOWED_DECISION_STATUSES))}")
    decision_id = get_next_decision_id()
    created_date = date.today().isoformat()
    cleaned_tags = [tag.strip() for tag in (tags or []) if str(tag).strip()]
    cleaned_tasks = [task.strip() for task in (related_tasks or []) if str(task).strip()]

    metadata = {
        "id": decision_id,
        "title": title,
        "status": status,
        "date": created_date,
        "tags": cleaned_tags,
        "related_tasks": cleaned_tasks,
        "file_path": (DECISIONS_DIR / f"{decision_id}.md").as_posix(),
    }

    markdown = "\n".join(
        [
            f"# {decision_id}: {title}",
            "",
            f"- Status: {status}",
            f"- Date: {created_date}",
            f"- Tags: {', '.join(cleaned_tags) if cleaned_tags else 'none'}",
            f"- Related Tasks: {', '.join(cleaned_tasks) if cleaned_tasks else 'none'}",
            "",
            "## Context",
            "",
            context,
            "",
            "## Decision",
            "",
            decision,
            "",
            "## Consequences",
            "",
            consequences,
            "",
        ]
    )

    _ensure_decisions_storage()
    _decision_file_path(decision_id).write_text(markdown, encoding="utf-8")
    index = load_decision_index()
    index.append(metadata)
    save_decision_index(index)
    return metadata


def read_decision_file(decision_id: str) -> str:
    decision = get_decision_by_id(decision_id)
    if decision is None:
        raise ValueError(f"Decision not found: {decision_id}")
    file_path = Path(str(decision.get("file_path", "")))
    if not file_path.exists():
        raise ValueError(f"Decision file is missing: {file_path.as_posix()}")
    return file_path.read_text(encoding="utf-8")


def link_decision_to_task(tasks: list[dict], task_id: str, decision_id: str) -> dict:
    decision = get_decision_by_id(decision_id)
    if decision is None:
        raise ValueError(f"Decision not found: {decision_id}")
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    task.setdefault("related_decisions", [])
    if decision_id not in task["related_decisions"]:
        task["related_decisions"].append(decision_id)
    return task


def unlink_decision_from_task(tasks: list[dict], task_id: str, decision_id: str) -> dict:
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    related = [item for item in task.get("related_decisions", []) if item != decision_id]
    task["related_decisions"] = related
    return task


def get_task_related_decisions(task: dict) -> list[dict]:
    related = []
    for decision_id in task.get("related_decisions", []):
        decision = get_decision_by_id(decision_id)
        if decision is not None:
            related.append(decision)
    return related
