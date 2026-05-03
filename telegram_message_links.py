"""
Persistent mapping: Telegram message -> work item (task/bug).

When a task/bug card is posted to the status chat we store
(chat_id, message_id) -> (work_item_type, work_item_id) so that
replies to those cards can be routed back to the correct context
without sending the full project state to the LLM first.

Storage: sessions/telegram_message_links.json  (same folder as sessions.json)
Format:  list of link dicts, appended on every new card post.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

_LINKS_PATH = Path("sessions") / "telegram_message_links.json"


def _load() -> list[dict]:
    if not _LINKS_PATH.exists():
        return []
    try:
        data = json.loads(_LINKS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(links: list[dict]) -> None:
    _LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LINKS_PATH.write_text(
        json.dumps(links, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_message_link(
    chat_id: str | int,
    message_id: int,
    work_item_type: str,
    work_item_id: str,
    message_thread_id: "int | None" = None,
) -> dict:
    """Store a new Telegram message -> work item mapping and return the entry.

    Args:
        chat_id:           Telegram chat id (positive for DM, negative for groups).
        message_id:        Telegram message id.
        work_item_type:    "task", "bug", "release", etc.
        work_item_id:      Item identifier e.g. "TASK-12".
        message_thread_id: Forum topic thread id (for Board messages). Optional.
    """
    links = _load()
    entry: dict = {
        "telegram_chat_id": str(chat_id),
        "telegram_message_id": int(message_id),
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if message_thread_id is not None:
        entry["message_thread_id"] = int(message_thread_id)
    links.append(entry)
    _save(links)
    return entry


def find_link(chat_id: str | int, message_id: int) -> dict | None:
    """Return the stored link for (chat_id, message_id), or None if not found."""
    target_chat = str(chat_id)
    target_msg = int(message_id)
    for entry in _load():
        if (
            str(entry.get("telegram_chat_id", "")) == target_chat
            and int(entry.get("telegram_message_id", -1)) == target_msg
        ):
            return entry
    return None


def find_board_link(work_item_type: str, work_item_id: str) -> dict | None:
    """Return the most recent Board card link for a work item, or None.

    Board links have a ``topic_key`` field that distinguishes them from
    status-chat links.  When multiple entries exist (e.g. after --force-new),
    the most recently created one is returned.
    """
    best: dict | None = None
    for entry in _load():
        if (
            entry.get("work_item_type") == work_item_type
            and entry.get("work_item_id") == work_item_id
            and entry.get("topic_key")  # Board links always have topic_key
        ):
            if best is None or entry.get("created_at", "") >= best.get("created_at", ""):
                best = entry
    return best


def upsert_board_link(
    chat_id: str | int,
    message_id: int,
    work_item_type: str,
    work_item_id: str,
    message_thread_id: "int | None",
    topic_key: str,
) -> dict:
    """Create or replace the Board card mapping for a work item.

    If a Board link already exists for (work_item_type, work_item_id) it is
    updated in-place (message_id, chat_id, updated_at refreshed).
    Otherwise a new entry is appended.

    Returns the final entry dict.
    """
    links = _load()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Find existing Board entry for this work item
    existing_idx: int | None = None
    best_created = ""
    for i, entry in enumerate(links):
        if (
            entry.get("work_item_type") == work_item_type
            and entry.get("work_item_id") == work_item_id
            and entry.get("topic_key")
        ):
            if entry.get("created_at", "") >= best_created:
                best_created = entry.get("created_at", "")
                existing_idx = i

    new_entry: dict = {
        "telegram_chat_id": str(chat_id),
        "telegram_message_id": int(message_id),
        "work_item_type": work_item_type,
        "work_item_id": work_item_id,
        "topic_key": topic_key,
        "created_at": now,
        "updated_at": now,
    }
    if message_thread_id is not None:
        new_entry["message_thread_id"] = int(message_thread_id)

    if existing_idx is not None:
        # Preserve original created_at, update the rest
        new_entry["created_at"] = links[existing_idx].get("created_at", now)
        links[existing_idx] = new_entry
    else:
        links.append(new_entry)

    _save(links)
    return new_entry


def load_all_links() -> list[dict]:
    return _load()
