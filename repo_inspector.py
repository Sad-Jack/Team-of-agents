from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

SAFE_TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
}

IGNORED_DIRECTORIES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
}

IGNORED_FILES = {
    ".env",
}


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root_path(repo_root: str = ".") -> Path:
    return Path(repo_root).resolve()


def _contains_forbidden_tokens(path: str) -> bool:
    return any(token in path for token in ("&&", "||", ";", "|", ">", "<", "`", "$("))


def is_safe_repo_path(path: str, repo_root: str = ".") -> bool:
    if not isinstance(path, str) or not path.strip():
        return False
    if _contains_forbidden_tokens(path):
        return False
    candidate = Path(path)
    if candidate.is_absolute():
        return False
    if ".." in candidate.parts:
        return False
    if candidate.name == ".env":
        return False
    if ".git" in candidate.parts:
        return False

    root = _repo_root_path(repo_root)
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def _is_ignored(relative_path: Path) -> bool:
    if any(part in IGNORED_DIRECTORIES for part in relative_path.parts):
        return True
    if relative_path.name in IGNORED_FILES:
        return True
    return False


def _is_safe_text_file(relative_path: Path) -> bool:
    return relative_path.suffix.lower() in SAFE_TEXT_EXTENSIONS


def scan_repository(repo_root: str = ".", max_files: int = 500) -> dict:
    root = _repo_root_path(repo_root)
    indexed = 0
    interesting_dirs = set()
    ignored_paths = []

    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if _is_ignored(rel):
            ignored_paths.append(rel.as_posix())
            continue
        if path.is_dir():
            if rel.parts:
                interesting_dirs.add(rel.parts[0])
            continue
        if not path.is_file():
            continue
        if not _is_safe_text_file(rel):
            continue
        indexed += 1
        if rel.parts:
            interesting_dirs.add(rel.parts[0])
        if indexed >= max_files:
            break

    return {
        "total_files_indexed": indexed,
        "interesting_directories": sorted(interesting_dirs),
        "ignored_paths": ignored_paths[:100],
    }


def list_repository_tree(repo_root: str = ".", max_depth: int = 4) -> List[str]:
    root = _repo_root_path(repo_root)
    items: List[str] = []
    depth_limit = max(0, int(max_depth))

    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if _is_ignored(rel):
            continue
        depth = len(rel.parts)
        if depth > depth_limit:
            continue
        suffix = "/" if path.is_dir() else ""
        items.append(rel.as_posix() + suffix)
    return items


def read_repository_file(path: str, repo_root: str = ".", max_chars: int = 4000) -> dict:
    if not is_safe_repo_path(path, repo_root=repo_root):
        raise ValueError(f"Unsafe repository path: {path}")

    rel = Path(path)
    if not _is_safe_text_file(rel):
        raise ValueError(f"Unsupported file type for preview: {path}")

    root = _repo_root_path(repo_root)
    full_path = (root / rel).resolve()
    if not full_path.exists() or not full_path.is_file():
        raise ValueError(f"Repository file not found: {path}")

    text = full_path.read_text(encoding="utf-8", errors="replace")
    limit = max(1, int(max_chars))
    preview = text[:limit]
    truncated = len(text) > limit
    return {
        "path": rel.as_posix(),
        "size_bytes": full_path.stat().st_size,
        "preview": preview,
        "truncated": truncated,
    }


def search_repository(query: str, repo_root: str = ".", max_results: int = 20) -> List[dict]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must be a non-empty string.")

    root = _repo_root_path(repo_root)
    query_l = query.lower()
    results: List[dict] = []
    limit = max(1, int(max_results))

    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if _is_ignored(rel):
            continue
        if not path.is_file() or not _is_safe_text_file(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            if query_l in line.lower():
                results.append(
                    {
                        "path": rel.as_posix(),
                        "line_number": idx,
                        "line": line[:1000],
                    }
                )
                if len(results) >= limit:
                    return results
    return results


def build_repository_context_for_task(task: dict, repo_root: str = ".") -> dict:
    artifacts = task.get("artifacts", {}) if isinstance(task, dict) else {}
    plan = artifacts.get("implementation_plan", {}) if isinstance(artifacts, dict) else {}
    patch = artifacts.get("patch_proposal", {}) if isinstance(artifacts, dict) else {}

    candidate_paths = []
    candidate_paths.extend(plan.get("files_to_modify", []) if isinstance(plan, dict) else [])
    candidate_paths.extend(plan.get("files_to_create", []) if isinstance(plan, dict) else [])
    if isinstance(patch, dict):
        for item in patch.get("files", []):
            if isinstance(item, dict):
                candidate_paths.append(item.get("file_path"))

    normalized_candidates = []
    for item in candidate_paths:
        if isinstance(item, str) and item.strip() and item.strip() != "unknown":
            normalized_candidates.append(item.strip())

    if not normalized_candidates:
        normalized_candidates = [
            "README.md",
            "AGENTS.md",
            "run.py",
            "orchestrator.py",
            "agent_runner.py",
        ]

    relevant_files = []
    for rel in normalized_candidates:
        if not is_safe_repo_path(rel, repo_root=repo_root):
            continue
        try:
            preview = read_repository_file(rel, repo_root=repo_root, max_chars=1200)
        except ValueError:
            continue
        relevant_files.append(
            {
                "path": preview["path"],
                "reason": "Referenced by task planning context.",
                "size_bytes": preview["size_bytes"],
                "preview": preview["preview"],
            }
        )

    summary = scan_repository(repo_root=repo_root)
    return {
        "attached": True,
        "scanned_at": _now_iso_utc(),
        "repo_root": ".",
        "summary": summary,
        "relevant_files": relevant_files,
        "search_hits": [],
    }
