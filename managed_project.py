from __future__ import annotations

import os
from pathlib import Path


class ManagedProjectError(Exception):
    pass


def get_system_root() -> str:
    return Path(__file__).resolve().parent.as_posix()


def get_managed_repo_path() -> str:
    return (os.getenv("MANAGED_REPO_PATH") or ".").strip() or "."


def resolve_managed_repo_path(path: str | None = None) -> str:
    configured = get_managed_repo_path() if path is None else (path.strip() if isinstance(path, str) else "")
    if not configured:
        configured = "."

    system_root = Path(get_system_root())
    resolved = (system_root / configured).resolve()

    if not resolved.exists():
        raise ManagedProjectError(f"Managed repository path does not exist: {resolved.as_posix()}")
    if not resolved.is_dir():
        raise ManagedProjectError(f"Managed repository path is not a directory: {resolved.as_posix()}")
    return resolved.as_posix()


def get_managed_project_info() -> dict:
    configured = get_managed_repo_path()
    system_root = Path(get_system_root())
    resolved = (system_root / configured).resolve()

    exists = resolved.exists()
    is_directory = resolved.is_dir()
    has_git = (resolved / ".git").exists() if is_directory else False
    has_readme = any((resolved / name).is_file() for name in ("README.md", "README.MD", "readme.md")) if is_directory else False

    sample_entries: list[str] = []
    if is_directory:
        try:
            sample_entries = sorted(item.name for item in resolved.iterdir())[:15]
        except OSError:
            sample_entries = []

    return {
        "system_root": system_root.as_posix(),
        "managed_repo_path": configured,
        "managed_repo_root": resolved.as_posix(),
        "exists": exists,
        "is_directory": is_directory,
        "has_git": has_git,
        "has_readme": has_readme,
        "sample_entries": sample_entries,
    }


def validate_managed_repo_path(path: str | None = None) -> dict:
    configured = get_managed_repo_path() if path is None else (path.strip() if isinstance(path, str) else "")
    if not configured:
        configured = "."

    system_root = Path(get_system_root())
    resolved = (system_root / configured).resolve()

    errors: list[str] = []
    warnings: list[str] = []

    exists = resolved.exists()
    is_directory = resolved.is_dir()

    if not exists:
        errors.append(f"Path does not exist: {resolved.as_posix()}")
    elif not is_directory:
        errors.append(f"Path is not a directory: {resolved.as_posix()}")

    has_git = (resolved / ".git").exists() if is_directory else False
    has_readme = any((resolved / name).is_file() for name in ("README.md", "README.MD", "readme.md")) if is_directory else False

    if resolved == system_root:
        warnings.append("Managed repository is the same as system root.")
    if is_directory and not has_readme:
        warnings.append("Managed repository has no README file.")
    if is_directory and not has_git:
        warnings.append("Managed repository has no .git directory.")

    sample_entries: list[str] = []
    if is_directory:
        try:
            sample_entries = sorted(item.name for item in resolved.iterdir())[:15]
        except OSError:
            sample_entries = []

    return {
        "system_root": system_root.as_posix(),
        "managed_repo_path": configured,
        "managed_repo_root": resolved.as_posix(),
        "exists": exists,
        "is_directory": is_directory,
        "has_git": has_git,
        "has_readme": has_readme,
        "sample_entries": sample_entries,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }
