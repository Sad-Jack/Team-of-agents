import os
from datetime import datetime, timezone

from managed_project import resolve_managed_repo_path


def get_patch_proposal(task: dict) -> dict:
    return task.get("artifacts", {}).get("patch_proposal", {})


def export_patch_proposal(task: dict, output_path: str, force: bool = False) -> str:
    proposal = get_patch_proposal(task)
    output_file = os.path.abspath(output_path)
    if os.path.exists(output_file) and not force:
        raise ValueError(f"Output file already exists: {output_file}. Use --force to overwrite.")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    lines = [
        f"# Patch Proposal for {task.get('id', 'UNKNOWN')}",
        "",
        "## Summary",
        "",
        proposal.get("summary", ""),
        "",
        "## Approved",
        "",
        str(proposal.get("approved", False)),
        "",
        "## Applied",
        "",
        str(proposal.get("applied", False)),
        "",
        "## Files",
        "",
    ]
    files = proposal.get("files", [])
    if files:
        for item in files:
            lines.append(f"- `{item.get('change_type', 'unknown')}` `{item.get('file_path', 'unknown')}`: {item.get('reason', '')}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Unified Diff", "", "```diff", proposal.get("unified_diff", ""), "```", ""])

    with open(output_file, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))
    return output_file


def approve_patch(task: dict) -> dict:
    proposal = get_patch_proposal(task)
    proposal["approved"] = True
    task["artifacts"]["patch_proposal"] = proposal
    return task


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_unsafe_path(path: str) -> bool:
    norm = path.replace("\\", "/")
    if not norm or os.path.isabs(path):
        return True
    parts = [p for p in norm.split("/") if p]
    if any(p == ".." for p in parts):
        return True
    if norm == ".env" or norm.startswith(".git/"):
        return True
    return False


def apply_patch_proposal(task: dict, repo_root: str | None = None, force: bool = False) -> dict:
    proposal = get_patch_proposal(task)
    result = {"applied_files": [], "skipped_files": [], "errors": []}

    if proposal.get("requires_approval", True) and not proposal.get("approved", False) and not force:
        result["errors"].append("Patch proposal is not approved.")
        return result

    if proposal.get("applied", False) and not force:
        result["errors"].append("Patch proposal is already applied.")
        return result

    root = os.path.abspath(resolve_managed_repo_path() if repo_root is None else repo_root)
    files = proposal.get("files", [])

    for item in files:
        rel_path = item.get("file_path", "")
        change_type = item.get("change_type")
        content = item.get("content", "")

        if _is_unsafe_path(rel_path):
            result["errors"].append(f"Unsafe path rejected: {rel_path}")
            continue

        abs_path = os.path.abspath(os.path.join(root, rel_path))
        if not abs_path.startswith(root + os.sep) and abs_path != root:
            result["errors"].append(f"Path escapes repository root: {rel_path}")
            continue

        try:
            if change_type == "create":
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                if os.path.exists(abs_path) and not force:
                    result["errors"].append(f"File exists and force not set: {rel_path}")
                    continue
                with open(abs_path, "w", encoding="utf-8") as file:
                    file.write(content)
                result["applied_files"].append(rel_path)

            elif change_type == "modify":
                if not os.path.exists(abs_path) and not force:
                    result["errors"].append(f"File does not exist for modify: {rel_path}")
                    continue
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as file:
                    file.write(content)
                result["applied_files"].append(rel_path)

            elif change_type == "delete":
                if not os.path.exists(abs_path):
                    result["errors"].append(f"File does not exist for delete: {rel_path}")
                    continue
                if os.path.isdir(abs_path):
                    result["errors"].append(f"Refusing to delete directory: {rel_path}")
                    continue
                os.remove(abs_path)
                result["applied_files"].append(rel_path)

            else:
                result["errors"].append(f"Unsupported change_type: {change_type}")
        except OSError as exc:
            result["errors"].append(f"Failed to apply {rel_path}: {exc}")

    if not result["errors"]:
        proposal["applied"] = True
        proposal["applied_at"] = _utc_now()
        task["artifacts"]["patch_proposal"] = proposal

    return result
