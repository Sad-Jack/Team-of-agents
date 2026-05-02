from pathlib import Path

PROJECT_CONTEXT_DIR = Path("project_context")
PROJECT_CONTEXT_FILES = [
    "project.md",
    "tech_stack.md",
    "architecture.md",
    "coding_rules.md",
    "testing_rules.md",
    "commands.md",
    "restrictions.md",
]


def list_project_context_files() -> list[str]:
    return list(PROJECT_CONTEXT_FILES)


def load_project_context() -> dict:
    context = {}
    for filename in PROJECT_CONTEXT_FILES:
        path = PROJECT_CONTEXT_DIR / filename
        if not path.exists():
            raise ValueError(f"Missing project context file: {path.as_posix()}")
        context[filename] = path.read_text(encoding="utf-8")
    return context


def load_project_context_text() -> str:
    context = load_project_context()
    parts = []
    for filename in PROJECT_CONTEXT_FILES:
        parts.append(f"# project_context/{filename}\n{context[filename].rstrip()}\n")
    return "\n".join(parts).rstrip() + "\n"
