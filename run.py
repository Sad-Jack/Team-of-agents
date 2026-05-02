import argparse
import json
import os
import sys

import backlog
import orchestrator
from command_runner import ALLOWED_COMMANDS, is_command_allowed, run_safe_command
from llm_client import LLMClientError, get_llm_client
from decision_log import (
    create_decision,
    get_decision_by_id,
    get_task_related_decisions,
    link_decision_to_task,
    list_decisions,
    read_decision_file,
    unlink_decision_from_task,
)
from orchestrator import (
    add_history_event,
    create_bug,
    create_task,
    get_task,
    get_task_implementation_plan,
    get_task_patch_proposal,
    list_available_agents,
    list_tasks,
    run_all_ready_tasks,
    run_next_for_task,
    validate_all_tasks,
)
from patch_utils import apply_patch_proposal, approve_patch, export_patch_proposal
from project_context_loader import load_project_context, load_project_context_text, list_project_context_files
from release_manager import (
    add_task_to_release,
    calculate_release_readiness,
    create_release,
    generate_release_notes,
    generate_release_risks,
    generate_rollback_plan,
    get_release_by_id,
    get_release_tasks,
    load_releases,
    remove_task_from_release,
    save_releases,
    set_release_status,
)
from repo_inspector import (
    build_repository_context_for_task,
    list_repository_tree,
    read_repository_file,
    scan_repository,
    search_repository,
)
from supervisor import (
    IMPLEMENTED_EXECUTION_ACTIONS,
    READ_ONLY_ACTIONS,
    RISKY_ACTIONS,
    SUPPORTED_ACTIONS,
    execute_supervisor_action,
    plan_supervisor_action,
)


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


def _format_qa_report_markdown(task_id: str, report: dict) -> str:
    lines = [
        f"# QA Report for {task_id}",
        "",
        "## Verdict",
        "",
        str(report.get("verdict", "unknown")),
        "",
        "## Summary",
        "",
        report.get("summary", ""),
        "",
    ]

    def add_list_section(title: str, values):
        lines.append(f"## {title}")
        lines.append("")
        if values:
            for item in values:
                lines.append(f"- {item}")
        else:
            lines.append("- (none)")
        lines.append("")

    add_list_section("Checked Items", report.get("checked_items", []))
    add_list_section("Failed Checks", report.get("failed_checks", []))
    add_list_section("Bugs Found", report.get("bugs_found", []))
    lines.append("## Recommended Next Status")
    lines.append("")
    lines.append(str(report.get("recommended_next_status", "review")))
    lines.append("")
    return "\n".join(lines)


def _load_task_for_update(task_id: str):
    tasks = orchestrator.load_tasks()
    task = None
    for item in tasks:
        if item.get("id") == task_id:
            task = item
            break
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    return tasks, task


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
        state = "blocked" if backlog.is_task_blocked(task, tasks) else "ready"
        print(f"{task['id']} | {task['type']} | {task['status']} | {task['priority']} | {state} | {task['title']}")


def cmd_show(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    print(json.dumps(task, ensure_ascii=False, indent=2))


def cmd_decisions(_args):
    items = list_decisions()
    if not items:
        print("No decisions found.")
        return
    for item in items:
        tags = ",".join(item.get("tags", []))
        related_tasks = ",".join(item.get("related_tasks", []))
        print(
            f"{item.get('id')} | {item.get('status')} | {item.get('date')} | "
            f"{item.get('title')} | tags=[{tags}] | related_tasks=[{related_tasks}]"
        )


def cmd_decision(args):
    meta = get_decision_by_id(args.id)
    if meta is None:
        raise ValueError(f"Decision not found: {args.id}")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print()
    print(read_decision_file(args.id))


def cmd_create_decision(args):
    tags = [tag.strip() for tag in (args.tags or "").split(",") if tag.strip()]
    related_tasks = args.related_task or []
    metadata = create_decision(
        title=args.title,
        context=args.context,
        decision=args.decision,
        consequences=args.consequences,
        status=args.status,
        tags=tags,
        related_tasks=related_tasks,
    )

    if related_tasks:
        tasks = orchestrator.load_tasks()
        for task_id in related_tasks:
            task = link_decision_to_task(tasks, task_id, metadata["id"])
            add_history_event(task, f"Linked decision {metadata['id']} to task.", agent="orchestrator")
        orchestrator.save_tasks(tasks)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def cmd_link_decision(args):
    tasks = orchestrator.load_tasks()
    task = link_decision_to_task(tasks, args.id, args.decision)
    add_history_event(task, f"Linked decision {args.decision} to task.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: linked decision {args.decision}")


def cmd_unlink_decision(args):
    tasks = orchestrator.load_tasks()
    task = unlink_decision_from_task(tasks, args.id, args.decision)
    add_history_event(task, f"Unlinked decision {args.decision} from task.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: unlinked decision {args.decision}")


def cmd_task_decisions(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    items = get_task_related_decisions(task)
    if not items:
        print("No decisions linked to this task.")
        return
    for item in items:
        print(f"{item.get('id')} | {item.get('status')} | {item.get('title')}")


def _print_backlog_rows(tasks: list[dict], include_reason: bool = True):
    for task in tasks:
        blocked = backlog.is_task_blocked(task, tasks)
        state = "blocked" if blocked else "ready"
        reason = backlog.get_blocked_reason(task, tasks) if blocked else ""
        depends = ",".join(task.get("depends_on", []))
        row = (
            f"{task['id']} | {task['type']} | {task['status']} | {task['priority']} | {state} | "
            f"depends_on=[{depends}] | release_id={task.get('release_id')} | {task['title']}"
        )
        if include_reason:
            row = f"{row} | blocked_reason={reason or task.get('blocked_reason', '')}"
        print(row)


def cmd_backlog(_args):
    tasks = backlog.sort_backlog(list_tasks())
    if not tasks:
        print("No tasks found.")
        return
    _print_backlog_rows(tasks, include_reason=True)


def cmd_ready(_args):
    tasks = list_tasks()
    ready_tasks = backlog.get_ready_tasks(tasks)
    if not ready_tasks:
        print("No ready tasks found.")
        return
    _print_backlog_rows(ready_tasks, include_reason=False)


def cmd_blocked(_args):
    tasks = list_tasks()
    blocked_tasks = backlog.get_blocked_tasks(tasks)
    if not blocked_tasks:
        print("No blocked tasks found.")
        return
    _print_backlog_rows(blocked_tasks, include_reason=True)


def cmd_next_task(_args):
    tasks = list_tasks()
    item = backlog.recommend_next_task(tasks)
    if item is None:
        print("No ready tasks found.")
        return
    print(json.dumps(item, ensure_ascii=False, indent=2))


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
    output_file = os.path.normpath(os.path.abspath(output_path))
    if os.path.exists(output_file) and not args.force:
        raise ValueError(f"Output file already exists: {output_file}. Use --force to overwrite.")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as file:
        file.write(_format_plan_markdown(args.id, plan))
    print(f"Exported developer plan to {output_file}")


def cmd_patch(args):
    proposal = get_task_patch_proposal(args.id)
    if proposal is None:
        raise ValueError(f"Task not found: {args.id}")
    print(json.dumps(proposal, ensure_ascii=False, indent=2))


def cmd_export_patch(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    output_path = args.output or os.path.join("artifacts", args.id, "patch.md")
    output = export_patch_proposal(task, output_path, force=args.force)
    print(f"Exported patch proposal to {output}")


def cmd_approve_patch(args):
    tasks, task = _load_task_for_update(args.id)
    approve_patch(task)
    add_history_event(task, "Patch proposal approved.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: patch proposal approved.")


def cmd_apply_patch(args):
    tasks, task = _load_task_for_update(args.id)
    result = apply_patch_proposal(task, repo_root=".", force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["errors"]:
        add_history_event(task, "Patch proposal applied.", agent="orchestrator")
        orchestrator.save_tasks(tasks)
        print(f"{args.id}: patch proposal applied successfully.")
    else:
        orchestrator.save_tasks(tasks)
        print(f"{args.id}: patch proposal not fully applied.")


def cmd_qa_report(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    report = task["artifacts"].get("qa_verification") or {}
    has_content = bool(report.get("summary", "").strip() or report.get("checked_items") or report.get("failed_checks"))
    if not has_content and report.get("verdict", "unknown") == "unknown":
        print("No QA verification report exists yet. Run QA step first.")
        return
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_export_qa_report(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    report = task["artifacts"].get("qa_verification") or {}
    has_content = bool(report.get("summary", "").strip() or report.get("checked_items") or report.get("failed_checks"))
    if not has_content and report.get("verdict", "unknown") == "unknown":
        print("No QA verification report exists yet. Run QA step first.")
        return

    output_path = args.output or os.path.join("artifacts", args.id, "qa_report.md")
    output_file = os.path.normpath(os.path.abspath(output_path))
    if os.path.exists(output_file) and not args.force:
        raise ValueError(f"Output file already exists: {output_file}. Use --force to overwrite.")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as file:
        file.write(_format_qa_report_markdown(args.id, report))
    print(f"Exported QA report to {output_file}")


def cmd_commands(_args):
    for command in sorted(ALLOWED_COMMANDS):
        print(command)


def cmd_run_command(args):
    tasks, task = _load_task_for_update(args.id)
    orchestrator.validate_task(task)
    result = run_safe_command(args.command, cwd=".", timeout_seconds=args.timeout)
    result["source"] = "manual"
    task["artifacts"]["command_results"].append(result)
    add_history_event(task, f"Executed command: {args.command}", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_run_plan_commands(args):
    tasks, task = _load_task_for_update(args.id)
    orchestrator.validate_task(task)
    commands = task["artifacts"]["implementation_plan"].get("commands_to_run", [])
    if not commands:
        print("No implementation plan commands to run.")
        return

    executed = []
    skipped = []
    for command in commands:
        if not isinstance(command, str) or not is_command_allowed(command):
            skipped.append(command)
            continue
        result = run_safe_command(command, cwd=".", timeout_seconds=args.timeout)
        result["source"] = "implementation_plan"
        task["artifacts"]["command_results"].append(result)
        executed.append(command)

    add_history_event(
        task,
        f"Executed implementation plan commands. executed={len(executed)} skipped={len(skipped)}",
        agent="orchestrator",
    )
    orchestrator.save_tasks(tasks)
    print(json.dumps({"executed": executed, "skipped": skipped}, ensure_ascii=False, indent=2))


def cmd_command_results(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    results = task["artifacts"].get("command_results", [])
    if not results:
        print("No command results recorded yet.")
        return
    for idx, item in enumerate(results, start=1):
        print(f"{idx}. command={item['command']}")
        print(f"   success={item['success']} exit_code={item['exit_code']} duration={item['duration_seconds']}")
        print(f"   stdout: {(item.get('stdout') or '')[:200]}")
        print(f"   stderr: {(item.get('stderr') or '')[:200]}")


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


def cmd_repo_scan(_args):
    summary = scan_repository(repo_root=".")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_repo_tree(args):
    entries = list_repository_tree(repo_root=".", max_depth=args.depth)
    for item in entries:
        print(item)


def cmd_repo_file(args):
    preview = read_repository_file(path=args.path, repo_root=".", max_chars=args.max_chars)
    print(json.dumps(preview, ensure_ascii=False, indent=2))


def cmd_repo_search(args):
    hits = search_repository(query=args.query, repo_root=".", max_results=args.limit)
    if not hits:
        print("No matches found.")
        return
    for hit in hits:
        print(f"{hit['path']}:{hit['line_number']} | {hit['line']}")


def cmd_attach_repo_context(args):
    tasks, task = _load_task_for_update(args.id)
    context = build_repository_context_for_task(task, repo_root=".")
    task["artifacts"]["repository_context"] = context
    add_history_event(task, "Attached repository context to task.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: repository context attached.")


def cmd_repo_context(args):
    task = get_task(args.id)
    if task is None:
        raise ValueError(f"Task not found: {args.id}")
    context = task["artifacts"].get("repository_context") or {}
    if not context.get("attached"):
        print("No repository context attached yet.")
        return
    print(json.dumps(context, ensure_ascii=False, indent=2))


def cmd_add_dependency(args):
    tasks, task = _load_task_for_update(args.id)
    backlog.add_dependency(tasks, args.id, args.depends_on)
    add_history_event(task, f"Added dependency: {args.id} depends on {args.depends_on}.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: added dependency on {args.depends_on}")


def cmd_remove_dependency(args):
    tasks, task = _load_task_for_update(args.id)
    backlog.remove_dependency(tasks, args.id, args.depends_on)
    add_history_event(task, f"Removed dependency: {args.id} no longer depends on {args.depends_on}.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: removed dependency on {args.depends_on}")


def cmd_block(args):
    tasks, task = _load_task_for_update(args.id)
    blocked_by = [item.strip() for item in args.blocked_by.split(",") if item.strip()]
    backlog.set_blocker(tasks, args.id, blocked_by=blocked_by, blocked_reason=args.reason)
    add_history_event(task, f"Task blocked by {blocked_by}: {args.reason}", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: blocker set.")


def cmd_unblock(args):
    tasks, task = _load_task_for_update(args.id)
    backlog.clear_blocker(tasks, args.id)
    add_history_event(task, "Task unblocked.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    print(f"{args.id}: blocker cleared.")


def _find_release_or_error(release_id: str):
    releases = load_releases()
    rel = get_release_by_id(releases, release_id)
    if rel is None:
        raise ValueError(f"Release not found: {release_id}")
    return releases, rel


def cmd_create_release(args):
    rel = create_release(name=args.name, description=args.description, target_date=args.target_date)
    print(f"Created release {rel['id']} ({rel['name']})")


def cmd_releases(_args):
    items = load_releases()
    if not items:
        print("No releases found.")
        return
    for rel in items:
        print(
            f"{rel['id']} | {rel['name']} | {rel['status']} | target_date={rel.get('target_date')} | "
            f"tasks={len(rel.get('tasks', []))} | {rel.get('description', '')}"
        )


def cmd_release(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    release_tasks = get_release_tasks(tasks, rel)
    readiness = calculate_release_readiness(tasks, rel)
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    print()
    print(f"Linked tasks: {[t['id'] for t in release_tasks]}")
    print(f"Readiness: ready={readiness['ready']} total={readiness['total_tasks']} done={readiness['done_tasks']}")


def cmd_add_to_release(args):
    tasks = orchestrator.load_tasks()
    releases = load_releases()
    add_task_to_release(tasks, releases, args.task, args.release)
    task = next(item for item in tasks if item.get("id") == args.task)
    add_history_event(task, f"Added task to release {args.release}.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    save_releases(releases)
    print(f"{args.task}: added to {args.release}")


def cmd_remove_from_release(args):
    tasks = orchestrator.load_tasks()
    releases = load_releases()
    remove_task_from_release(tasks, releases, args.task, args.release)
    task = next(item for item in tasks if item.get("id") == args.task)
    add_history_event(task, f"Removed task from release {args.release}.", agent="orchestrator")
    orchestrator.save_tasks(tasks)
    save_releases(releases)
    print(f"{args.task}: removed from {args.release}")


def cmd_release_status(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    readiness = calculate_release_readiness(tasks, rel)
    print(f"{rel['id']} status={rel['status']} ready={readiness['ready']} summary={readiness['summary']}")


def cmd_release_readiness(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    readiness = calculate_release_readiness(tasks, rel)
    print(json.dumps(readiness, ensure_ascii=False, indent=2))


def cmd_release_notes(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    print(generate_release_notes(tasks, rel))


def cmd_export_release_notes(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    output_path = args.output or os.path.join("artifacts", rel["id"], "release_notes.md")
    output_file = os.path.normpath(os.path.abspath(output_path))
    if os.path.exists(output_file) and not args.force:
        raise ValueError(f"Output file already exists: {output_file}. Use --force to overwrite.")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as file:
        file.write(generate_release_notes(tasks, rel))
    print(f"Exported release notes to {output_file}")


def cmd_release_risks(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    risks = generate_release_risks(tasks, rel)
    if not risks:
        print("No risks identified.")
        return
    for item in risks:
        print(f"- {item}")


def cmd_rollback_plan(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    print(generate_rollback_plan(tasks, rel))


def cmd_export_rollback_plan(args):
    tasks = orchestrator.load_tasks()
    _, rel = _find_release_or_error(args.id)
    output_path = args.output or os.path.join("artifacts", rel["id"], "rollback_plan.md")
    output_file = os.path.normpath(os.path.abspath(output_path))
    if os.path.exists(output_file) and not args.force:
        raise ValueError(f"Output file already exists: {output_file}. Use --force to overwrite.")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as file:
        file.write(generate_rollback_plan(tasks, rel))
    print(f"Exported rollback plan to {output_file}")


def cmd_set_release_status(args):
    releases = load_releases()
    set_release_status(releases, args.id, args.status)
    save_releases(releases)
    print(f"{args.id}: status set to {args.status}")


def cmd_supervisor_actions(_args):
    for action in sorted(SUPPORTED_ACTIONS):
        mode = "read-only" if action in READ_ONLY_ACTIONS else "write"
        risky = "yes" if action in RISKY_ACTIONS else "no"
        implemented = "yes" if action in IMPLEMENTED_EXECUTION_ACTIONS else "no"
        print(f"{action} | mode={mode} | risky={risky} | execute_implemented={implemented}")


def cmd_supervise(args):
    plan = plan_supervisor_action(args.text)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        return
    if plan["action"]["name"] in RISKY_ACTIONS and not args.yes:
        raise ValueError(
            f"Refusing risky supervisor action '{plan['action']['name']}' without --yes confirmation."
        )
    result = execute_supervisor_action(plan, allow_risky=args.yes)
    print(json.dumps(result, ensure_ascii=False, indent=2))


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

    decisions_parser = subparsers.add_parser("decisions", help="List decision records")
    decisions_parser.set_defaults(func=cmd_decisions)

    decision_parser = subparsers.add_parser("decision", help="Show one decision record")
    decision_parser.add_argument("--id", required=True)
    decision_parser.set_defaults(func=cmd_decision)

    create_decision_parser = subparsers.add_parser("create-decision", help="Create new ADR decision")
    create_decision_parser.add_argument("--title", required=True)
    create_decision_parser.add_argument("--context", required=True)
    create_decision_parser.add_argument("--decision", required=True)
    create_decision_parser.add_argument("--consequences", required=True)
    create_decision_parser.add_argument(
        "--status",
        required=False,
        default="accepted",
        choices=["proposed", "accepted", "superseded", "rejected"],
    )
    create_decision_parser.add_argument("--tags", required=False, default="")
    create_decision_parser.add_argument("--related-task", action="append")
    create_decision_parser.set_defaults(func=cmd_create_decision)

    link_decision_parser = subparsers.add_parser("link-decision", help="Link decision to task")
    link_decision_parser.add_argument("--id", required=True)
    link_decision_parser.add_argument("--decision", required=True)
    link_decision_parser.set_defaults(func=cmd_link_decision)

    unlink_decision_parser = subparsers.add_parser("unlink-decision", help="Unlink decision from task")
    unlink_decision_parser.add_argument("--id", required=True)
    unlink_decision_parser.add_argument("--decision", required=True)
    unlink_decision_parser.set_defaults(func=cmd_unlink_decision)

    task_decisions_parser = subparsers.add_parser("task-decisions", help="List decisions linked to task")
    task_decisions_parser.add_argument("--id", required=True)
    task_decisions_parser.set_defaults(func=cmd_task_decisions)

    backlog_parser = subparsers.add_parser("backlog", help="Show backlog sorted by priority/readiness")
    backlog_parser.set_defaults(func=cmd_backlog)

    ready_parser = subparsers.add_parser("ready", help="Show ready tasks")
    ready_parser.set_defaults(func=cmd_ready)

    blocked_parser = subparsers.add_parser("blocked", help="Show blocked tasks")
    blocked_parser.set_defaults(func=cmd_blocked)

    next_parser = subparsers.add_parser("next-task", help="Recommend next ready task")
    next_parser.set_defaults(func=cmd_next_task)

    create_release_parser = subparsers.add_parser("create-release", help="Create a release package")
    create_release_parser.add_argument("--name", required=True)
    create_release_parser.add_argument("--description", required=False, default="")
    create_release_parser.add_argument("--target-date", required=False, default=None)
    create_release_parser.set_defaults(func=cmd_create_release)

    releases_parser = subparsers.add_parser("releases", help="List releases")
    releases_parser.set_defaults(func=cmd_releases)

    release_parser = subparsers.add_parser("release", help="Show release details")
    release_parser.add_argument("--id", required=True)
    release_parser.set_defaults(func=cmd_release)

    add_to_release_parser = subparsers.add_parser("add-to-release", help="Link task to release")
    add_to_release_parser.add_argument("--release", required=True)
    add_to_release_parser.add_argument("--task", required=True)
    add_to_release_parser.set_defaults(func=cmd_add_to_release)

    remove_from_release_parser = subparsers.add_parser("remove-from-release", help="Unlink task from release")
    remove_from_release_parser.add_argument("--release", required=True)
    remove_from_release_parser.add_argument("--task", required=True)
    remove_from_release_parser.set_defaults(func=cmd_remove_from_release)

    release_status_parser = subparsers.add_parser("release-status", help="Show release status/readiness summary")
    release_status_parser.add_argument("--id", required=True)
    release_status_parser.set_defaults(func=cmd_release_status)

    release_readiness_parser = subparsers.add_parser("release-readiness", help="Show full release readiness report")
    release_readiness_parser.add_argument("--id", required=True)
    release_readiness_parser.set_defaults(func=cmd_release_readiness)

    release_notes_parser = subparsers.add_parser("release-notes", help="Generate release notes markdown")
    release_notes_parser.add_argument("--id", required=True)
    release_notes_parser.set_defaults(func=cmd_release_notes)

    export_release_notes_parser = subparsers.add_parser("export-release-notes", help="Export release notes markdown")
    export_release_notes_parser.add_argument("--id", required=True)
    export_release_notes_parser.add_argument("--output", required=False)
    export_release_notes_parser.add_argument("--force", action="store_true")
    export_release_notes_parser.set_defaults(func=cmd_export_release_notes)

    release_risks_parser = subparsers.add_parser("release-risks", help="Show generated release risks")
    release_risks_parser.add_argument("--id", required=True)
    release_risks_parser.set_defaults(func=cmd_release_risks)

    rollback_plan_parser = subparsers.add_parser("rollback-plan", help="Generate rollback plan")
    rollback_plan_parser.add_argument("--id", required=True)
    rollback_plan_parser.set_defaults(func=cmd_rollback_plan)

    export_rollback_parser = subparsers.add_parser("export-rollback-plan", help="Export rollback plan markdown")
    export_rollback_parser.add_argument("--id", required=True)
    export_rollback_parser.add_argument("--output", required=False)
    export_rollback_parser.add_argument("--force", action="store_true")
    export_rollback_parser.set_defaults(func=cmd_export_rollback_plan)

    set_release_status_parser = subparsers.add_parser("set-release-status", help="Set release status")
    set_release_status_parser.add_argument("--id", required=True)
    set_release_status_parser.add_argument(
        "--status",
        required=True,
        choices=["planned", "in_progress", "ready", "released", "cancelled"],
    )
    set_release_status_parser.set_defaults(func=cmd_set_release_status)

    supervisor_actions_parser = subparsers.add_parser("supervisor-actions", help="List supervisor supported actions")
    supervisor_actions_parser.set_defaults(func=cmd_supervisor_actions)

    supervise_parser = subparsers.add_parser("supervise", help="Plan or execute action from natural language")
    supervise_parser.add_argument("--text", required=True)
    supervise_parser.add_argument("--execute", action="store_true")
    supervise_parser.add_argument("--yes", action="store_true")
    supervise_parser.set_defaults(func=cmd_supervise)

    dev_plan_parser = subparsers.add_parser("dev-plan", help="Show developer implementation plan for a task")
    dev_plan_parser.add_argument("--id", required=True)
    dev_plan_parser.set_defaults(func=cmd_dev_plan)

    export_plan_parser = subparsers.add_parser("export-dev-plan", help="Export developer implementation plan to markdown")
    export_plan_parser.add_argument("--id", required=True)
    export_plan_parser.add_argument("--output", required=False)
    export_plan_parser.add_argument("--force", action="store_true")
    export_plan_parser.set_defaults(func=cmd_export_dev_plan)

    patch_parser = subparsers.add_parser("patch", help="Show patch proposal for a task")
    patch_parser.add_argument("--id", required=True)
    patch_parser.set_defaults(func=cmd_patch)

    export_patch_parser = subparsers.add_parser("export-patch", help="Export patch proposal to markdown")
    export_patch_parser.add_argument("--id", required=True)
    export_patch_parser.add_argument("--output", required=False)
    export_patch_parser.add_argument("--force", action="store_true")
    export_patch_parser.set_defaults(func=cmd_export_patch)

    approve_patch_parser = subparsers.add_parser("approve-patch", help="Approve patch proposal")
    approve_patch_parser.add_argument("--id", required=True)
    approve_patch_parser.set_defaults(func=cmd_approve_patch)

    apply_patch_parser = subparsers.add_parser("apply-patch", help="Apply patch proposal")
    apply_patch_parser.add_argument("--id", required=True)
    apply_patch_parser.add_argument("--force", action="store_true")
    apply_patch_parser.set_defaults(func=cmd_apply_patch)

    qa_report_parser = subparsers.add_parser("qa-report", help="Show QA verification report for a task")
    qa_report_parser.add_argument("--id", required=True)
    qa_report_parser.set_defaults(func=cmd_qa_report)

    export_qa_parser = subparsers.add_parser("export-qa-report", help="Export QA report to markdown")
    export_qa_parser.add_argument("--id", required=True)
    export_qa_parser.add_argument("--output", required=False)
    export_qa_parser.add_argument("--force", action="store_true")
    export_qa_parser.set_defaults(func=cmd_export_qa_report)

    commands_parser = subparsers.add_parser("commands", help="List allowed local commands")
    commands_parser.set_defaults(func=cmd_commands)

    run_command_parser = subparsers.add_parser("run-command", help="Run a single allowlisted command")
    run_command_parser.add_argument("--id", required=True)
    run_command_parser.add_argument("--command", required=True)
    run_command_parser.add_argument("--timeout", type=int, default=30)
    run_command_parser.set_defaults(func=cmd_run_command)

    run_plan_commands_parser = subparsers.add_parser("run-plan-commands", help="Run allowlisted implementation plan commands")
    run_plan_commands_parser.add_argument("--id", required=True)
    run_plan_commands_parser.add_argument("--timeout", type=int, default=30)
    run_plan_commands_parser.set_defaults(func=cmd_run_plan_commands)

    command_results_parser = subparsers.add_parser("command-results", help="Show command execution results for a task")
    command_results_parser.add_argument("--id", required=True)
    command_results_parser.set_defaults(func=cmd_command_results)

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

    repo_scan_parser = subparsers.add_parser("repo-scan", help="Scan repository safely and show summary")
    repo_scan_parser.set_defaults(func=cmd_repo_scan)

    repo_tree_parser = subparsers.add_parser("repo-tree", help="List repository tree with safe filters")
    repo_tree_parser.add_argument("--depth", type=int, default=4)
    repo_tree_parser.set_defaults(func=cmd_repo_tree)

    repo_file_parser = subparsers.add_parser("repo-file", help="Preview a safe repository file")
    repo_file_parser.add_argument("--path", required=True)
    repo_file_parser.add_argument("--max-chars", type=int, default=4000)
    repo_file_parser.set_defaults(func=cmd_repo_file)

    repo_search_parser = subparsers.add_parser("repo-search", help="Search text in safe repository files")
    repo_search_parser.add_argument("--query", required=True)
    repo_search_parser.add_argument("--limit", type=int, default=20)
    repo_search_parser.set_defaults(func=cmd_repo_search)

    attach_repo_parser = subparsers.add_parser("attach-repo-context", help="Attach repository context to task")
    attach_repo_parser.add_argument("--id", required=True)
    attach_repo_parser.set_defaults(func=cmd_attach_repo_context)

    repo_context_parser = subparsers.add_parser("repo-context", help="Show attached repository context for task")
    repo_context_parser.add_argument("--id", required=True)
    repo_context_parser.set_defaults(func=cmd_repo_context)

    add_dependency_parser = subparsers.add_parser("add-dependency", help="Add task dependency")
    add_dependency_parser.add_argument("--id", required=True)
    add_dependency_parser.add_argument("--depends-on", required=True)
    add_dependency_parser.set_defaults(func=cmd_add_dependency)

    remove_dependency_parser = subparsers.add_parser("remove-dependency", help="Remove task dependency")
    remove_dependency_parser.add_argument("--id", required=True)
    remove_dependency_parser.add_argument("--depends-on", required=True)
    remove_dependency_parser.set_defaults(func=cmd_remove_dependency)

    block_parser = subparsers.add_parser("block", help="Set task blocker")
    block_parser.add_argument("--id", required=True)
    block_parser.add_argument("--blocked-by", required=True)
    block_parser.add_argument("--reason", required=True)
    block_parser.set_defaults(func=cmd_block)

    unblock_parser = subparsers.add_parser("unblock", help="Clear task blocker")
    unblock_parser.add_argument("--id", required=True)
    unblock_parser.set_defaults(func=cmd_unblock)

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
