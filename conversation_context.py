from __future__ import annotations

import re
from datetime import datetime, timezone

import orchestrator
from decision_log import get_decision_by_id
from release_manager import get_release_by_id, load_releases
from storage import load_collection, save_collection


class ConversationContextError(Exception):
    pass


TASK_ID_RE = re.compile(r"\b(?:TASK|BUG)-\d+\b", re.IGNORECASE)
REL_ID_RE = re.compile(r"\bREL-\d+\b", re.IGNORECASE)
ADR_ID_RE = re.compile(r"\bADR-\d+\b", re.IGNORECASE)
TASK_PRONOUN_RE = re.compile(r"\b(е[её]|его|ней|нему|this task|it|this)\b", re.IGNORECASE)
TASK_RELATED_HINT_RE = re.compile(
    r"\b(задач|task|подготов|prepare|продвин|advance|заметк|note|что по|status)\b",
    re.IGNORECASE,
)
MAX_RECENT_MESSAGES = 20


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_sessions() -> list[dict]:
    return load_collection("sessions")


def save_sessions(sessions: list[dict]) -> None:
    save_collection("sessions", sessions)


def _base_session(session_id: str, user_id: str, channel: str) -> dict:
    return {
        "session_id": session_id,
        "user_id": user_id or "",
        "channel": channel or "cli",
        "active_task_id": None,
        "active_release_id": None,
        "active_decision_id": None,
        "last_intent": None,
        "last_action": None,
        "last_updated_at": _now_iso_utc(),
        "recent_messages": [],
    }


def _validate_task_exists(task_id: str) -> None:
    if orchestrator.get_task(task_id) is None:
        raise ConversationContextError(f"Task not found: {task_id}")


def _validate_release_exists(release_id: str) -> None:
    releases = load_releases()
    if get_release_by_id(releases, release_id) is None:
        raise ConversationContextError(f"Release not found: {release_id}")


def _validate_decision_exists(decision_id: str) -> None:
    if get_decision_by_id(decision_id) is None:
        raise ConversationContextError(f"Decision not found: {decision_id}")


def get_or_create_session(session_id: str, user_id: str = "", channel: str = "cli") -> dict:
    sessions = load_sessions()
    for item in sessions:
        if item.get("session_id") == session_id:
            if user_id and not item.get("user_id"):
                item["user_id"] = user_id
            if channel and not item.get("channel"):
                item["channel"] = channel
            return item
    session = _base_session(session_id=session_id, user_id=user_id, channel=channel)
    sessions.append(session)
    save_sessions(sessions)
    return session


def save_session(session: dict) -> dict:
    sessions = load_sessions()
    session_id = session.get("session_id")
    if not session_id:
        raise ConversationContextError("Session must have session_id.")
    session["last_updated_at"] = _now_iso_utc()
    recent = session.get("recent_messages") or []
    session["recent_messages"] = recent[-MAX_RECENT_MESSAGES:]

    for idx, item in enumerate(sessions):
        if item.get("session_id") == session_id:
            sessions[idx] = session
            save_sessions(sessions)
            return session
    sessions.append(session)
    save_sessions(sessions)
    return session


def set_active_task(session_id: str, task_id: str, user_id: str = "", channel: str = "cli") -> dict:
    task_id = str(task_id or "").upper()
    _validate_task_exists(task_id)
    session = get_or_create_session(session_id, user_id=user_id, channel=channel)
    session["active_task_id"] = task_id
    session["active_release_id"] = None
    session["active_decision_id"] = None
    return save_session(session)


def set_active_release(session_id: str, release_id: str, user_id: str = "", channel: str = "cli") -> dict:
    release_id = str(release_id or "").upper()
    _validate_release_exists(release_id)
    session = get_or_create_session(session_id, user_id=user_id, channel=channel)
    session["active_release_id"] = release_id
    return save_session(session)


def set_active_decision(session_id: str, decision_id: str, user_id: str = "", channel: str = "cli") -> dict:
    decision_id = str(decision_id or "").upper()
    _validate_decision_exists(decision_id)
    session = get_or_create_session(session_id, user_id=user_id, channel=channel)
    session["active_decision_id"] = decision_id
    return save_session(session)


def clear_focus(session_id: str, user_id: str = "", channel: str = "cli") -> dict:
    session = get_or_create_session(session_id, user_id=user_id, channel=channel)
    session["active_task_id"] = None
    session["active_release_id"] = None
    session["active_decision_id"] = None
    return save_session(session)


def get_focus(session_id: str, user_id: str = "", channel: str = "cli") -> dict:
    session = get_or_create_session(session_id, user_id=user_id, channel=channel)
    task_id = session.get("active_task_id")
    release_id = session.get("active_release_id")
    decision_id = session.get("active_decision_id")

    if task_id:
        summary = f"Фокус: задача {task_id}"
    elif release_id:
        summary = f"Фокус: релиз {release_id}"
    elif decision_id:
        summary = f"Фокус: решение {decision_id}"
    else:
        summary = "Фокус не установлен."

    return {
        "active_task_id": task_id,
        "active_release_id": release_id,
        "active_decision_id": decision_id,
        "summary": summary,
    }


def append_message(
    session_id: str,
    role: str,
    text: str,
    user_id: str = "",
    channel: str = "cli",
) -> dict:
    session = get_or_create_session(session_id, user_id=user_id, channel=channel)
    recent = session.get("recent_messages") or []
    recent.append(
        {
            "timestamp": _now_iso_utc(),
            "role": str(role),
            "text": str(text),
        }
    )
    session["recent_messages"] = recent[-MAX_RECENT_MESSAGES:]
    return save_session(session)


def resolve_reference(text: str, session: dict) -> dict:
    value = str(text or "")
    task_match = TASK_ID_RE.search(value)
    if task_match:
        return {"task_id": task_match.group(0).upper(), "release_id": None, "decision_id": None}

    rel_match = REL_ID_RE.search(value)
    if rel_match:
        return {"task_id": None, "release_id": rel_match.group(0).upper(), "decision_id": None}

    adr_match = ADR_ID_RE.search(value)
    if adr_match:
        return {"task_id": None, "release_id": None, "decision_id": adr_match.group(0).upper()}

    active_task = session.get("active_task_id")
    if active_task and (TASK_PRONOUN_RE.search(value) or TASK_RELATED_HINT_RE.search(value)):
        return {"task_id": active_task, "release_id": None, "decision_id": None}

    return {"task_id": None, "release_id": None, "decision_id": None}
