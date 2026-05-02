import argparse
import json
import os
import sys

from llm_client import LLMClientError, get_llm_client
from orchestrator import (
    create_bug,
    create_task,
    get_task,
    list_available_agents,
    list_tasks,
    get_task_implementation_plan,
    run_all_ready_tasks,
    run_next_for_task,
    validate_all_tasks,
)
from project_context_loader import load_project_context, load_project_context_text, list_project_context_files


def _format_plan_markdown(task_id: str, plan: dict) -> str:
    lines = [f"# Developer Plan for {task_id}", "", "## Summary", "", plan.get("summary", ""), ""]

    def add_list_section(title: str, values):
        lines.append(f"## {title}")
        lines.append("")
        if values:
            for item in values:
                lines.append(f"- {item}")
        else:
            lines.append("- (none)")
        lines.append("")

    add_list_section("Files to Create", plan.get("files_to_create", []))
    add_list_section("Files to Modify", plan.get("files_to_modify", []))

    lines.append("## Proposed Changes")
    lines.append("")
    proposed = plan.get("proposed_changes", [])
    if proposed:
        for idx, change in enumerate(proposed, start=1):
            lines.append(f"{idx}. `{change.get('change_type', 'unknown')}` `{change.get('file_path', 'unknown')}`")
            lines.append(f"   - Reason: {change.get('reason', '')}")
            lines.append(f"   - Description: {change.get('description', '')}")
            lines.append(f"   - Safe to Apply: {change.get('safe_to_apply', False)}")
    else:
        lines.append("- (none)")
    lines.append("")

    add_list_section("Commands to Run", plan.get("commands_to_run", []))
    add_list_section("Tests to Add", plan.get("tests_to_add", []))
    add_list_section("Risks", plan.get("risks", []))

    lines.append("## Rollback Notes")
    lines.append("")
    lines.append(plan.get("rollback_notes", ""))
    lines.append("")
    return "\n".join(lines)


def cmd_create(args):
    task = create_task(args.title, args.description)
    print(f"Created {task['id']} with status={task['status']}")


def cmd_create_bug(args):
    raw_text = args.raw
    if args.raw_file:
        try:
            raw_text = (raw_text + "\n" if raw_text else "") + open(args.raw_file, "r", encoding="utf-8").read()
        except OSError as exc:
            raise ValueError(f"Failed to read --raw-file: {exc}") from exc

    llm_client = get_llm_client(args.provider) if args.provider else get_llm_client()
    bug = create_bug(
        title=args.title,
        description=args.description,
        raw_input=raw_text,
        priority=args.priority,
        severity=args.severity,
        llm_client=llm_client,
    )
    validate_all_tasks()
    print(f"Created {bug['id']} with status={bug['status']}")


def cmd_list(_args):
    tasks = list_tasks()
    if not tasks:
        print("No tasks found.")
        return
    for task in tasks:
        print(f"{task['id']} | {task['type']} | {task['status']} | {task['priority']} | {task['title']}")


def cmd_show(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    print(json.dumps(task, ensure_ascii=False, indent=2))


def cmd_dev_plan(args):
    plan = get_task_implementation_plan(args.id)
    if plan is None:
        raise ValueError(f"Task not found: {args.id}")
    if not plan.get("summary", "").strip() and not plan.get("proposed_changes"):
        print("No implementation plan exists yet. Run developer step first.")
        return
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def cmd_export_dev_plan(args):
    plan = get_task_implementation_plan(args.id)
    if plan is None:
        raise ValueError(f"Task not found: {args.id}")
    if not plan.get("summary", "").strip() and not plan.get("proposed_changes"):
        print("No implementation plan exists yet. Run developer step first.")
        return

    output_path = args.output or os.path.join("artifacts", args.id, "developer_plan.md")
    output = os.path.abspath(output_path)
    output_file = os.path.normpath(output)

    if os.path.exists(output_file) and not args.force:
        raise ValueError(f"Output file already exists: {output_file}. Use --force to overwrite.")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as file:
        file.write(_format_plan_markdown(args.id, plan))
    print(f"Exported developer plan to {output_file}")


def cmd_run_next(args):
    llm_client = get_llm_client(args.provider) if args.provider else get_llm_client()
    task, message = run_next_for_task(args.id, llm_client=llm_client)
    if task is None:
        raise ValueError(message)
    print(f"{task['id']} moved to {task['status']}: {message}")


def cmd_run_all(args):
    llm_client = get_llm_client(args.provider) if args.provider else get_llm_client()
    processed = run_all_ready_tasks(llm_client=llm_client)
    if not processed:
        print("No tasks processed.")
        return
    for item in processed:
        print(f"{item['id']} -> {item['status']}: {item['message']}")


def cmd_validate(_args):
    count = validate_all_tasks()
    print(f"Validation passed: {count} task(s) are valid.")


def cmd_agents(_args):
    agents = list_available_agents()
    for item in agents:
        print(f"{item['agent']} | {item['prompt_source']} | exists={item['exists']}")


def cmd_config(_args):
    provider = (os.getenv("LLM_PROVIDER") or "fake").strip().lower()
    model = (os.getenv("OPENAI_MODEL") or "gpt-5.1-mini").strip() or "gpt-5.1-mini"
    has_key = bool((os.getenv("OPENAI_API_KEY") or "").strip())

    print(f"LLM_PROVIDER={provider}")
    print(f"OPENAI_MODEL={model}")
    print(f"OPENAI_API_KEY_SET={str(has_key).lower()}")


def cmd_context(args):
    files = list_project_context_files()
    context = load_project_context()

    if args.show:
        print(load_project_context_text().rstrip())
        return

    for filename in files:
        text = context[filename].strip()
        preview = text.splitlines()[0] if text else "(empty)"
        print(f"{filename} | {preview}")


def build_parser():
    parser = argparse.ArgumentParser(description="Multi-agent task orchestrator MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new feature task")
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--description", required=True)
    create_parser.set_defaults(func=cmd_create)

    create_bug_parser = subparsers.add_parser("create-bug", help="Create a new bug task")
    create_bug_parser.add_argument("--title", required=True)
    create_bug_parser.add_argument("--description", required=True)
    create_bug_parser.add_argument("--raw", required=False, default="")
    create_bug_parser.add_argument("--raw-file", required=False)
    create_bug_parser.add_argument("--priority", required=False, default="medium")
    create_bug_parser.add_argument("--severity", required=False, default="unknown")
    create_bug_parser.add_argument("--provider", choices=["fake", "openai"], required=False)
    create_bug_parser.set_defaults(func=cmd_create_bug)

    list_parser = subparsers.add_parser("list", help="List tasks")
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show task details")
    show_parser.add_argument("--id", required=True)
    show_parser.set_defaults(func=cmd_show)

    dev_plan_parser = subparsers.add_parser("dev-plan", help="Show developer implementation plan for a task")
    dev_plan_parser.add_argument("--id", required=True)
    dev_plan_parser.set_defaults(func=cmd_dev_plan)

    export_plan_parser = subparsers.add_parser("export-dev-plan", help="Export developer implementation plan to markdown")
    export_plan_parser.add_argument("--id", required=True)
    export_plan_parser.add_argument("--output", required=False)
    export_plan_parser.add_argument("--force", action="store_true")
    export_plan_parser.set_defaults(func=cmd_export_dev_plan)

    run_next_parser = subparsers.add_parser("run-next", help="Run next step for a task")
    run_next_parser.add_argument("--id", required=True)
    run_next_parser.add_argument("--provider", choices=["fake", "openai"], required=False)
    run_next_parser.set_defaults(func=cmd_run_next)

    run_all_parser = subparsers.add_parser("run-all", help="Run one step for all non-done tasks")
    run_all_parser.add_argument("--provider", choices=["fake", "openai"], required=False)
    run_all_parser.set_defaults(func=cmd_run_all)

    validate_parser = subparsers.add_parser("validate", help="Validate all tasks")
    validate_parser.set_defaults(func=cmd_validate)

    agents_parser = subparsers.add_parser("agents", help="List available agent prompt files")
    agents_parser.set_defaults(func=cmd_agents)

    config_parser = subparsers.add_parser("config", help="Show effective LLM config")
    config_parser.set_defaults(func=cmd_config)

    context_parser = subparsers.add_parser("context", help="Show project context files")
    context_parser.add_argument("--show", action="store_true", help="Print full project context text")
    context_parser.set_defaults(func=cmd_context)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except (ValueError, OSError, LLMClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
