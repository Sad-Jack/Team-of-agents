from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class StorageError(Exception):
    pass


JSON_COLLECTION_PATHS = {
    "tasks": Path("tasks") / "tasks.json",
    "releases": Path("releases") / "releases.json",
    "decisions_index": Path("decisions") / "index.json",
    "sessions": Path("sessions") / "sessions.json",
}

ALLOWED_COLLECTIONS = set(JSON_COLLECTION_PATHS.keys())
ALLOWED_BACKENDS = {"json", "sqlite"}


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_storage_backend() -> str:
    backend = (os.getenv("STORAGE_BACKEND") or "json").strip().lower()
    if backend not in ALLOWED_BACKENDS:
        raise StorageError(f"Invalid STORAGE_BACKEND: {backend}. Allowed: json, sqlite")
    return backend


def get_sqlite_db_path() -> str:
    raw = (os.getenv("SQLITE_DB_PATH") or "data/team_agents.db").strip() or "data/team_agents.db"
    path = Path(raw)
    if path.is_absolute():
        return path.as_posix()
    return path.as_posix()


def ensure_storage_dirs() -> None:
    for path in JSON_COLLECTION_PATHS.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    sqlite_path = Path(get_sqlite_db_path())
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_json_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


def _ensure_sqlite_schema() -> None:
    sqlite_path = Path(get_sqlite_db_path())
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path.as_posix()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collections (
              name TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def init_storage() -> None:
    ensure_storage_dirs()
    backend = get_storage_backend()
    if backend == "json":
        for path in JSON_COLLECTION_PATHS.values():
            _ensure_json_file(path)
        return
    _ensure_sqlite_schema()
    with sqlite3.connect(Path(get_sqlite_db_path()).as_posix()) as conn:
        for name in sorted(ALLOWED_COLLECTIONS):
            conn.execute(
                "INSERT OR IGNORE INTO collections(name, payload, updated_at) VALUES (?, ?, ?)",
                (name, "[]", _now_iso_utc()),
            )
        conn.commit()


def _assert_collection_name(collection_name: str) -> None:
    if collection_name not in ALLOWED_COLLECTIONS:
        raise StorageError(f"Unsupported collection name: {collection_name}")


def _load_json_collection(collection_name: str) -> list[dict]:
    path = JSON_COLLECTION_PATHS[collection_name]
    _ensure_json_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageError(f"Invalid JSON in {path.as_posix()}: {exc}") from exc
    if not isinstance(data, list):
        raise StorageError(f"JSON collection must be an array: {path.as_posix()}")
    return data


def _save_json_collection(collection_name: str, items: list[dict]) -> None:
    if not isinstance(items, list):
        raise StorageError("Collection payload must be a list.")
    path = JSON_COLLECTION_PATHS[collection_name]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_sqlite_collection(collection_name: str) -> list[dict]:
    _ensure_sqlite_schema()
    sqlite_path = Path(get_sqlite_db_path())
    with sqlite3.connect(sqlite_path.as_posix()) as conn:
        row = conn.execute("SELECT payload FROM collections WHERE name = ?", (collection_name,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT OR IGNORE INTO collections(name, payload, updated_at) VALUES (?, ?, ?)",
                (collection_name, "[]", _now_iso_utc()),
            )
            conn.commit()
            return []
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError as exc:
        raise StorageError(f"Invalid JSON payload in sqlite collection '{collection_name}': {exc}") from exc
    if not isinstance(data, list):
        raise StorageError(f"SQLite collection '{collection_name}' must contain JSON array payload.")
    return data


def _save_sqlite_collection(collection_name: str, items: list[dict]) -> None:
    if not isinstance(items, list):
        raise StorageError("Collection payload must be a list.")
    _ensure_sqlite_schema()
    sqlite_path = Path(get_sqlite_db_path())
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    with sqlite3.connect(sqlite_path.as_posix()) as conn:
        conn.execute(
            """
            INSERT INTO collections(name, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (collection_name, payload, _now_iso_utc()),
        )
        conn.commit()


def load_collection(collection_name: str) -> list[dict]:
    _assert_collection_name(collection_name)
    backend = get_storage_backend()
    if backend == "json":
        return _load_json_collection(collection_name)
    return _load_sqlite_collection(collection_name)


def save_collection(collection_name: str, items: list[dict]) -> None:
    _assert_collection_name(collection_name)
    backend = get_storage_backend()
    if backend == "json":
        _save_json_collection(collection_name, items)
        return
    _save_sqlite_collection(collection_name, items)


def storage_info() -> dict:
    backend = get_storage_backend()
    init_storage()
    info = {
        "backend": backend,
        "sqlite_db_path": get_sqlite_db_path(),
        "collections": {},
    }
    for name in sorted(ALLOWED_COLLECTIONS):
        items = load_collection(name)
        info["collections"][name] = {"count": len(items)}
    return info


def migrate_json_to_sqlite(overwrite: bool = False) -> dict:
    ensure_storage_dirs()
    sqlite_path = Path(get_sqlite_db_path())
    _ensure_sqlite_schema()

    json_data = {name: _load_json_collection(name) for name in sorted(ALLOWED_COLLECTIONS)}

    with sqlite3.connect(sqlite_path.as_posix()) as conn:
        for name, items in json_data.items():
            row = conn.execute("SELECT payload FROM collections WHERE name = ?", (name,)).fetchone()
            if row is not None:
                existing = json.loads(row[0])
                if existing and not overwrite:
                    raise StorageError(
                        f"SQLite collection '{name}' already has data. Use --force to overwrite."
                    )
            conn.execute(
                """
                INSERT INTO collections(name, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (name, json.dumps(items, ensure_ascii=False, indent=2), _now_iso_utc()),
            )
        conn.commit()

    return {"backend": "sqlite", "sqlite_db_path": sqlite_path.as_posix(), "counts": {k: len(v) for k, v in json_data.items()}}


def export_sqlite_to_json(overwrite: bool = False) -> dict:
    ensure_storage_dirs()
    _ensure_sqlite_schema()
    sqlite_data = {name: _load_sqlite_collection(name) for name in sorted(ALLOWED_COLLECTIONS)}

    for name, items in sqlite_data.items():
        path = JSON_COLLECTION_PATHS[name]
        if path.exists() and not overwrite:
            current = _load_json_collection(name)
            if current:
                raise StorageError(f"JSON file '{path.as_posix()}' already has data. Use --force to overwrite.")
        _save_json_collection(name, items)

    return {"backend": "json", "counts": {k: len(v) for k, v in sqlite_data.items()}}
