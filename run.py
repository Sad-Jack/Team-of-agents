import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import backlog
import decision_log
import orchestrator
import release_manager
from command_runner import ALLOWED_COMMANDS, is_command_allowed, run_safe_command
from conversation_context import (
    append_message,
    clear_focus,
    get_focus,
    get_or_create_session,
    load_sessions,
    set_active_decision,
    set_active_release,
    set_active_task,
)
from decision_log import load_decision_index
from llm_client import LLMClientError, get_llm_client
from managed_project import get_managed_project_info, get_managed_repo_path, validate_managed_repo_path
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
from project_manager import (
    add_task_note,
    advance_task_safely,
    get_blockers_summary,
    get_next_work_recommendation,
    get_project_status,
    get_release_summary,
    get_task_status,
    list_task_notes,
    prepare_task_for_development,
    summarize_task_discussion,
)
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
import storage
import speech_to_text
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

CRITICAL_REQUIRED_DIRECTORIES = [
    "agents",
    "project_context",
    "tasks",
    "releases",
    "decisions",
    "artifacts",
    "docs",
]

CRITICAL_REQUIRED_FILES = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "tasks/tasks.json",
    "releases/releases.json",
    "decisions/index.json",
    "agents/analyst.md",
    "agents/architect.md",
    "agents/developer.md",
    "agents/qa.md",
    "agents/bug_intake.md",
    "agents/supervisor.md",
]


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
    result = run_safe_command(args.command, cwd=None, timeout_seconds=args.timeout)
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
        result = run_safe_command(command, cwd=None, timeout_seconds=args.timeout)
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
    claude_binary = (os.getenv("CLAUDE_CODE_BINARY") or "claude").strip() or "claude"
    claude_timeout = (os.getenv("CLAUDE_CODE_TIMEOUT_SECONDS") or "120").strip() or "120"
    anthropic_key_set = bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())
    storage_backend = (os.getenv("STORAGE_BACKEND") or "json").strip().lower()
    sqlite_db_path = (os.getenv("SQLITE_DB_PATH") or "data/team_agents.db").strip() or "data/team_agents.db"
    stt_provider = (os.getenv("STT_PROVIDER") or "disabled").strip().lower()
    ffmpeg_binary = (os.getenv("FFMPEG_BINARY") or "ffmpeg").strip() or "ffmpeg"
    voice_work_dir = (os.getenv("VOICE_WORK_DIR") or ".tmp/voice").strip() or ".tmp/voice"
    whisper_binary = (os.getenv("WHISPER_CLI_BINARY") or "whisper").strip() or "whisper"
    whisper_model = (os.getenv("WHISPER_MODEL") or "small").strip() or "small"
    whisper_language = (os.getenv("WHISPER_LANGUAGE") or "ru").strip() or "ru"
    voice_keep_files = (os.getenv("VOICE_KEEP_FILES") or "false").strip().lower() in {"1", "true", "yes", "y", "on"}
    stt_custom_set = bool((os.getenv("STT_CUSTOM_COMMAND") or "").strip())
    managed_path = get_managed_repo_path()
    managed_validation = validate_managed_repo_path()

    print(f"LLM_PROVIDER={provider}")
    print(f"STORAGE_BACKEND={storage_backend}")
    print(f"SQLITE_DB_PATH={sqlite_db_path}")
    print(f"MANAGED_REPO_PATH={managed_path}")
    print(f"MANAGED_REPO_ROOT={managed_validation.get('managed_repo_root')}")
    print(f"CLAUDE_CODE_BINARY={claude_binary}")
    print(f"CLAUDE_CODE_TIMEOUT_SECONDS={claude_timeout}")
    print(f"ANTHROPIC_API_KEY_SET={str(anthropic_key_set).lower()}")
    print(f"OPENAI_MODEL={model}")
    print(f"OPENAI_API_KEY_SET={str(has_key).lower()}")
    print(f"STT_PROVIDER={stt_provider}")
    print(f"FFMPEG_BINARY={ffmpeg_binary}")
    print(f"VOICE_WORK_DIR={voice_work_dir}")
    print(f"WHISPER_CLI_BINARY={whisper_binary}")
    print(f"WHISPER_MODEL={whisper_model}")
    print(f"WHISPER_LANGUAGE={whisper_language}")
    print(f"VOICE_KEEP_FILES={str(voice_keep_files).lower()}")
    print(f"STT_CUSTOM_COMMAND_SET={str(stt_custom_set).lower()}")
    if provider == "claude_code" and anthropic_key_set:
        print(
            "WARNING: ANTHROPIC_API_KEY is set. Claude Code may use API billing instead of subscription "
            "depending on Claude Code auth/config."
        )


def cmd_llm_smoke(args):
    client = get_llm_client()
    payload = {
        "agent_name": "smoke",
        "user_text": args.prompt,
        "prompt": args.prompt,
    }
    text = client.generate(payload)
    print(text)


def cmd_telegram_config(_args):
    token_set = bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip())
    owner_set = bool((os.getenv("TELEGRAM_OWNER_ID") or "").strip())
    dry_run_default = (os.getenv("TELEGRAM_DRY_RUN_BY_DEFAULT") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    status_chat_set = bool((os.getenv("TELEGRAM_STATUS_CHAT_ID") or "").strip())
    fast_router_enabled = (os.getenv("TELEGRAM_FAST_ROUTER_ENABLED") or "true").strip().lower() in {
        "1", "true", "yes", "y", "on"
    }
    board_enabled = (os.getenv("TELEGRAM_BOARD_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes", "y", "on"
    }
    board_chat_set = bool((os.getenv("TELEGRAM_BOARD_CHAT_ID") or "").strip())
    print(f"TELEGRAM_BOT_TOKEN_SET={str(token_set).lower()}")
    print(f"TELEGRAM_OWNER_ID_SET={str(owner_set).lower()}")
    print(f"TELEGRAM_DRY_RUN_BY_DEFAULT={str(dry_run_default).lower()}")
    print(f"TELEGRAM_STATUS_CHAT_ID_SET={str(status_chat_set).lower()}")
    print(f"TELEGRAM_FAST_ROUTER_ENABLED={str(fast_router_enabled).lower()}")
    print(f"TELEGRAM_BOARD_ENABLED={str(board_enabled).lower()}")
    print(f"TELEGRAM_BOARD_CHAT_ID_SET={str(board_chat_set).lower()}")
    _board_topics = [
        "TASK_IDEAS", "TASK_READY", "TASK_ACTIVE", "TASK_BLOCKED",
        "BUGS_NEW", "BUGS_ACTIVE", "NEEDS_INPUT",
        "RELEASES", "AGENT_LOG", "DECISIONS",
    ]
    for _t in _board_topics:
        _set = bool((os.getenv(f"TELEGRAM_TOPIC_{_t}") or "").strip())
        print(f"TELEGRAM_TOPIC_{_t}_SET={str(_set).lower()}")
    print(f"STT_PROVIDER={(os.getenv('STT_PROVIDER') or 'disabled').strip().lower()}")
    print(f"FFMPEG_BINARY={(os.getenv('FFMPEG_BINARY') or 'ffmpeg').strip() or 'ffmpeg'}")
    print(f"VOICE_WORK_DIR={(os.getenv('VOICE_WORK_DIR') or '.tmp/voice').strip() or '.tmp/voice'}")
    print(f"WHISPER_CLI_BINARY={(os.getenv('WHISPER_CLI_BINARY') or 'whisper').strip() or 'whisper'}")
    print(f"WHISPER_MODEL={(os.getenv('WHISPER_MODEL') or 'small').strip() or 'small'}")
    print(f"WHISPER_LANGUAGE={(os.getenv('WHISPER_LANGUAGE') or 'ru').strip() or 'ru'}")
    voice_keep = (os.getenv('VOICE_KEEP_FILES') or 'false').strip().lower() in {'1','true','yes','y','on'}
    print(f"VOICE_KEEP_FILES={str(voice_keep).lower()}")
    print(f"STT_CUSTOM_COMMAND_SET={str(bool((os.getenv('STT_CUSTOM_COMMAND') or '').strip())).lower()}")


def cmd_board_config(_args):
    """Human-readable Telegram Board diagnostics.

    Shows topic IDs (not secrets) and SET flags for chat_id.
    Does not print TELEGRAM_BOT_TOKEN or absolute paths.
    """
    _TOPIC_KEYS = [
        ("task_ideas",   "TELEGRAM_TOPIC_TASK_IDEAS",   "task ideas"),
        ("task_ready",   "TELEGRAM_TOPIC_TASK_READY",   "task ready"),
        ("task_active",  "TELEGRAM_TOPIC_TASK_ACTIVE",  "task active"),
        ("task_blocked", "TELEGRAM_TOPIC_TASK_BLOCKED", "task blocked"),
        ("bugs_new",     "TELEGRAM_TOPIC_BUGS_NEW",     "bugs new"),
        ("bugs_active",  "TELEGRAM_TOPIC_BUGS_ACTIVE",  "bugs active"),
        ("needs_input",  "TELEGRAM_TOPIC_NEEDS_INPUT",  "needs input"),
        ("releases",     "TELEGRAM_TOPIC_RELEASES",     "releases"),
        ("agent_log",    "TELEGRAM_TOPIC_AGENT_LOG",    "agent log"),
        ("decisions",    "TELEGRAM_TOPIC_DECISIONS",    "decisions"),
    ]

    enabled = (os.getenv("TELEGRAM_BOARD_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes", "y", "on"
    }
    chat_id_set = bool((os.getenv("TELEGRAM_BOARD_CHAT_ID") or "").strip())

    print("Telegram Board configuration:")
    print(f"- enabled: {str(enabled).lower()}")
    print(f"- board chat configured: {str(chat_id_set).lower()}")
    print()
    print("Topics:")

    missing: list[str] = []
    if not chat_id_set:
        missing.append("TELEGRAM_BOARD_CHAT_ID")

    for _key, env_name, label in _TOPIC_KEYS:
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            # topic ids are not secrets — show the value
            try:
                tid = int(raw)
                print(f"- {label}: {tid}")
            except ValueError:
                print(f"- {label}: (invalid: {raw!r})")
                missing.append(env_name)
        else:
            print(f"- {label}: (not set)")
            missing.append(env_name)

    print()
    print("Status:")
    if enabled and not missing:
        print("✅ Telegram Board is configured")
    elif not enabled:
        print("❌ Telegram Board is disabled (TELEGRAM_BOARD_ENABLED=false)")
        if missing:
            print("Missing:")
            for m in missing:
                print(f"- {m}")
    else:
        print("❌ Telegram Board is not fully configured")
        print("Missing:")
        for m in missing:
            print(f"- {m}")


def cmd_board_ping(args):
    """Smoke-test Telegram Board topics.

    Sends a ping message to every configured topic (or --topic <key> for one).
    With --dry-run shows what would be sent without calling the Telegram API.
    Does not print TELEGRAM_BOT_TOKEN or absolute paths.
    """
    import telegram_board

    dry_run: bool = getattr(args, "dry_run", False)
    topic_key: str | None = getattr(args, "topic", None) or None
    board_enabled = (os.getenv("TELEGRAM_BOARD_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes", "y", "on"
    }
    board_chat_id = (os.getenv("TELEGRAM_BOARD_CHAT_ID") or "").strip()

    # Validate --topic key before doing anything else
    valid_keys = {k for k, _, _ in telegram_board.BOARD_TOPICS}
    if topic_key is not None and topic_key not in valid_keys:
        print(f"Error: unknown topic key {topic_key!r}")
        print("Available topic keys:")
        for k, name, _ in telegram_board.BOARD_TOPICS:
            print(f"  {k}  ({name})")
        raise SystemExit(1)

    # Build the subset to show/ping
    topics_to_ping = [
        (k, n, e) for k, n, e in telegram_board.BOARD_TOPICS
        if topic_key is None or k == topic_key
    ]

    if dry_run:
        scope = f"topic={topic_key}" if topic_key else "all topics"
        print(f"Board ping — dry run (no messages sent) [{scope}]")
        print()
        print(f"TELEGRAM_BOARD_ENABLED: {str(board_enabled).lower()}")
        print(f"TELEGRAM_BOARD_CHAT_ID: {'(set)' if board_chat_id else '(not set)'}")
        timeout_sec = telegram_board.get_send_timeout()
        print(f"TELEGRAM_BOARD_SEND_TIMEOUT_SECONDS: {timeout_sec:.0f}")
        print()
        print("Topics:")
        missing = []
        for key, name, env_name in topics_to_ping:
            tid = telegram_board.get_topic_id(key)
            if tid is not None:
                print(f"  ✅ {name}: would send to thread {tid}")
            else:
                print(f"  — {name}: {env_name} not set (would skip)")
                missing.append(env_name)
        if missing:
            print()
            print("Missing env vars:")
            for m in missing:
                print(f"  - {m}")
        return

    # Live ping
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN is not set")
        raise SystemExit(1)

    if not board_enabled:
        print("Telegram Board is disabled (TELEGRAM_BOARD_ENABLED=false)")
        raise SystemExit(1)

    if not board_chat_id:
        print("Error: TELEGRAM_BOARD_CHAT_ID is not set")
        raise SystemExit(1)

    import asyncio

    async def _run_ping():
        try:
            from telegram import Bot
        except ImportError:
            print("Error: python-telegram-bot is not installed")
            raise SystemExit(1)
        bot = Bot(token=token)
        try:
            results = await telegram_board.ping_board_topics(bot, topic_filter=topic_key)
        except ValueError as exc:
            print(f"Error: {exc}")
            raise SystemExit(1)
        print(telegram_board.format_ping_results(results))
        has_hard_error = any(r["status"] == "error" for r in results)
        if has_hard_error:
            raise SystemExit(1)

    asyncio.run(_run_ping())


def cmd_managed_project(_args):
    info = validate_managed_repo_path()
    print("Управляемый проект:")
    print(f"- system_root: {info['system_root']}")
    print(f"- configured_path: {info['managed_repo_path']}")
    print(f"- resolved_root: {info['managed_repo_root']}")
    print(f"- exists: {'true' if info['exists'] else 'false'}")
    print(f"- is_directory: {'true' if info['is_directory'] else 'false'}")
    print(f"- has_git: {'true' if info['has_git'] else 'false'}")
    print(f"- has_readme: {'true' if info['has_readme'] else 'false'}")
    print(f"- sample_entries: {', '.join(info['sample_entries']) if info['sample_entries'] else '(empty)'}")
    if info["warnings"]:
        print("warnings:")
        for item in info["warnings"]:
            print(f"- {item}")
    if info["errors"]:
        print("errors:")
        for item in info["errors"]:
            print(f"- {item}")


def cmd_managed_project_check(_args):
    info = validate_managed_repo_path()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if not info.get("valid"):
        raise ValueError("Managed project path validation failed.")


def cmd_telegram(_args):
    from telegram_bot import load_telegram_config, start_polling_bot

    load_telegram_config()
    print("Telegram bot started in polling mode.")
    start_polling_bot()


def cmd_voice_config(_args):
    provider = speech_to_text.get_stt_provider()
    ffmpeg_binary = (os.getenv("FFMPEG_BINARY") or "ffmpeg").strip() or "ffmpeg"
    work_dir = speech_to_text.get_voice_work_dir()
    whisper_binary = (os.getenv("WHISPER_CLI_BINARY") or "whisper").strip() or "whisper"
    whisper_model = (os.getenv("WHISPER_MODEL") or "small").strip() or "small"
    whisper_language = (os.getenv("WHISPER_LANGUAGE") or "ru").strip() or "ru"
    voice_keep = speech_to_text.should_keep_voice_files()
    custom_set = bool((os.getenv("STT_CUSTOM_COMMAND") or "").strip())

    print(f"STT_PROVIDER={provider}")
    print(f"FFMPEG_BINARY={ffmpeg_binary}")
    print(f"FFMPEG_FOUND={str(bool(shutil.which(ffmpeg_binary))).lower()}")
    print(f"VOICE_WORK_DIR={work_dir}")
    print(f"WHISPER_CLI_BINARY={whisper_binary}")
    print(f"WHISPER_MODEL={whisper_model}")
    print(f"WHISPER_LANGUAGE={whisper_language}")
    print(f"VOICE_KEEP_FILES={str(voice_keep).lower()}")
    print(f"STT_CUSTOM_COMMAND_SET={str(custom_set).lower()}")
    if provider == "whisper_cli":
        print(f"WHISPER_BINARY_FOUND={str(bool(shutil.which(whisper_binary))).lower()}")
    if provider == "custom_cli":
        cmd = (os.getenv("STT_CUSTOM_COMMAND") or "").strip()
        first = shlex.split(cmd)[0] if cmd else ""
        print(f"CUSTOM_BINARY_FOUND={str(bool(first and shutil.which(first))).lower()}")


def cmd_transcribe_file(args):
    input_path = Path(args.path).resolve()
    if not input_path.exists() or not input_path.is_file():
        raise ValueError(f"Audio file not found: {input_path.as_posix()}")
    work_dir = speech_to_text.ensure_voice_work_dir()
    wav_path = work_dir / f"manual_{input_path.stem}.wav"
    cleanup_paths = [wav_path.as_posix()]
    transcript = None
    try:
        speech_to_text.convert_voice_to_wav(input_path.as_posix(), wav_path.as_posix())
        transcript = speech_to_text.transcribe_audio(wav_path.as_posix())
        print(transcript)
    finally:
        if transcript is None or not speech_to_text.should_keep_voice_files():
            speech_to_text.cleanup_voice_files(cleanup_paths)


def _collect_doctor_report(repo_root: str = ".") -> dict:
    root = Path(repo_root).resolve()
    dirs_status = {item: (root / item).is_dir() for item in CRITICAL_REQUIRED_DIRECTORIES}
    files_status = {item: (root / item).is_file() for item in CRITICAL_REQUIRED_FILES}

    tasks_count = None
    tasks_error = None
    try:
        tasks_count = validate_all_tasks()
    except Exception as exc:  # pragma: no cover - surfaced in report
        tasks_error = str(exc)

    release_count = None
    releases_error = None
    try:
        release_count = len(load_releases())
    except Exception as exc:  # pragma: no cover
        releases_error = str(exc)

    decisions_count = None
    decisions_error = None
    try:
        decisions_count = len(load_decision_index())
    except Exception as exc:  # pragma: no cover
        decisions_error = str(exc)

    provider = (os.getenv("LLM_PROVIDER") or "fake").strip().lower()
    claude_binary = (os.getenv("CLAUDE_CODE_BINARY") or "claude").strip() or "claude"
    claude_configured = bool(claude_binary)

    telegram_token_set = bool((os.getenv("TELEGRAM_BOT_TOKEN") or "").strip())
    telegram_owner_set = bool((os.getenv("TELEGRAM_OWNER_ID") or "").strip())
    telegram_dry = (os.getenv("TELEGRAM_DRY_RUN_BY_DEFAULT") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    openai_key_set = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    managed_project = validate_managed_repo_path()

    storage_backend = None
    storage_error = None
    storage_counts = {}
    sqlite_db_path = (os.getenv("SQLITE_DB_PATH") or "data/team_agents.db").strip() or "data/team_agents.db"
    sqlite_exists = Path(sqlite_db_path).exists()
    try:
        storage_backend = storage.get_storage_backend()
        storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
        storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
        storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH
        storage.JSON_COLLECTION_PATHS["sessions"] = Path("sessions") / "sessions.json"
        storage.init_storage()
        info = storage.storage_info()
        storage_counts = {k: v.get("count", 0) for k, v in info.get("collections", {}).items()}
    except Exception as exc:
        storage_error = str(exc)

    critical_ok = (
        all(dirs_status.values())
        and all(files_status.values())
        and tasks_error is None
        and releases_error is None
        and decisions_error is None
        and storage_error is None
        and managed_project.get("valid", False)
    )

    warnings = []
    if not telegram_token_set:
        warnings.append("TELEGRAM_BOT_TOKEN не задан.")
    if not telegram_owner_set:
        warnings.append("TELEGRAM_OWNER_ID не задан.")
    if provider == "claude_code" and not claude_configured:
        warnings.append("Claude Code бинарь не настроен.")
    if not openai_key_set:
        warnings.append("OPENAI_API_KEY не задан (не критично).")
    if storage_error is not None:
        warnings.append(f"Storage backend error: {storage_error}")
    warnings.extend(managed_project.get("warnings", []))

    return {
        "root": root.as_posix(),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "cwd": Path.cwd().as_posix(),
        "dirs_status": dirs_status,
        "files_status": files_status,
        "tasks_count": tasks_count,
        "tasks_error": tasks_error,
        "release_count": release_count,
        "releases_error": releases_error,
        "decisions_count": decisions_count,
        "decisions_error": decisions_error,
        "llm_provider": provider,
        "claude_binary": claude_binary,
        "claude_configured": claude_configured,
        "storage_backend": storage_backend,
        "sqlite_db_path": sqlite_db_path,
        "sqlite_exists": sqlite_exists,
        "storage_counts": storage_counts,
        "storage_error": storage_error,
        "json_files_present": {
            "tasks": files_status.get("tasks/tasks.json", False),
            "releases": files_status.get("releases/releases.json", False),
            "decisions_index": files_status.get("decisions/index.json", False),
            "sessions": (root / "sessions" / "sessions.json").is_file(),
        },
        "telegram_token_set": telegram_token_set,
        "telegram_owner_set": telegram_owner_set,
        "telegram_dry_run_default": telegram_dry,
        "managed_project": managed_project,
        "warnings": warnings,
        "critical_ok": critical_ok,
    }


def cmd_doctor(_args):
    report = _collect_doctor_report(".")
    print("=== Doctor: проверка готовности MVP ===")
    print(f"Python executable: {report['python_executable']}")
    print(f"Python version: {report['python_version']}")
    print(f"Рабочая директория: {report['cwd']}")
    print()

    print("Проверка директорий:")
    for name, ok in report["dirs_status"].items():
        print(f"- {name}: {'OK' if ok else 'MISSING'}")
    print()

    print("Проверка файлов:")
    for name, ok in report["files_status"].items():
        print(f"- {name}: {'OK' if ok else 'MISSING'}")
    print()

    print("Проверка задач:")
    if report["tasks_error"] is None:
        print(f"- validate_all_tasks: OK, задач={report['tasks_count']}")
    else:
        print(f"- validate_all_tasks: ERROR: {report['tasks_error']}")
    print("Проверка релизов:")
    if report["releases_error"] is None:
        print(f"- load_releases: OK, релизов={report['release_count']}")
    else:
        print(f"- load_releases: ERROR: {report['releases_error']}")
    print("Проверка решений:")
    if report["decisions_error"] is None:
        print(f"- load_decision_index: OK, решений={report['decisions_count']}")
    else:
        print(f"- load_decision_index: ERROR: {report['decisions_error']}")
    print()

    print("LLM конфигурация:")
    print(f"- LLM_PROVIDER: {report['llm_provider']}")
    print(f"- CLAUDE_CODE_BINARY: {report['claude_binary']}")
    print(f"- Claude configured: {'true' if report['claude_configured'] else 'false'}")
    print()
    print("Управляемый проект:")
    managed = report["managed_project"]
    print(f"- system_root: {managed['system_root']}")
    print(f"- MANAGED_REPO_PATH: {managed['managed_repo_path']}")
    print(f"- resolved_root: {managed['managed_repo_root']}")
    print(f"- exists: {'true' if managed['exists'] else 'false'}")
    print(f"- is_directory: {'true' if managed['is_directory'] else 'false'}")
    print(f"- has_git: {'true' if managed['has_git'] else 'false'}")
    print(f"- has_readme: {'true' if managed['has_readme'] else 'false'}")
    if managed["errors"]:
        print("- errors:")
        for item in managed["errors"]:
            print(f"  - {item}")
    if managed["warnings"]:
        print("- warnings:")
        for item in managed["warnings"]:
            print(f"  - {item}")
    print()
    print("Storage:")
    print(f"- STORAGE_BACKEND: {report.get('storage_backend')}")
    print(f"- SQLITE_DB_PATH: {report.get('sqlite_db_path')}")
    print(f"- SQLITE_DB_EXISTS: {'true' if report.get('sqlite_exists') else 'false'}")
    print(
        "- JSON_FILES_PRESENT: "
        f"tasks={'true' if report['json_files_present'].get('tasks') else 'false'}, "
        f"releases={'true' if report['json_files_present'].get('releases') else 'false'}, "
        f"decisions_index={'true' if report['json_files_present'].get('decisions_index') else 'false'}, "
        f"sessions={'true' if report['json_files_present'].get('sessions') else 'false'}"
    )
    if report.get("storage_error") is None:
        print(
            f"- collections: tasks={report['storage_counts'].get('tasks', 0)}, "
            f"releases={report['storage_counts'].get('releases', 0)}, "
            f"decisions_index={report['storage_counts'].get('decisions_index', 0)}, "
            f"sessions={report['storage_counts'].get('sessions', 0)}"
        )
    else:
        print(f"- ERROR: {report.get('storage_error')}")
    print()

    print("Telegram конфигурация:")
    print(f"- TELEGRAM_BOT_TOKEN_SET: {'true' if report['telegram_token_set'] else 'false'}")
    print(f"- TELEGRAM_OWNER_ID_SET: {'true' if report['telegram_owner_set'] else 'false'}")
    print(f"- TELEGRAM_DRY_RUN_BY_DEFAULT: {'true' if report['telegram_dry_run_default'] else 'false'}")
    print()

    if report["warnings"]:
        print("Предупреждения:")
        for item in report["warnings"]:
            print(f"- {item}")
        print()

    print("Подсказка для тестов:")
    print("python3 -m unittest discover -s tests")
    print()

    if report["critical_ok"]:
        print("✅ Система готова к локальному использованию")
        return
    print("❌ Найдены проблемы")
    raise ValueError("Doctor обнаружил критические проблемы.")


def cmd_demo_reset(args):
    if not args.yes:
        raise ValueError("Команда demo-reset разрушительная. Повторите с --yes.")
    orchestrator.save_tasks([])
    save_releases([])
    from decision_log import save_decision_index

    save_decision_index([])
    artifacts_dir = Path("artifacts")
    if artifacts_dir.exists():
        for item in artifacts_dir.iterdir():
            if item.name.startswith("DEMO-"):
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
    print("Demo-данные сброшены.")


def cmd_demo_seed(_args):
    cmd_demo_reset(argparse.Namespace(yes=True))
    task = create_task("Добавить healthcheck-команду", "Добавить CLI-команду для проверки состояния системы.")
    tasks = orchestrator.load_tasks()
    task_ref = next(item for item in tasks if item["id"] == task["id"])
    task_ref["priority"] = "high"
    task_ref["tags"] = ["demo", "mvp", "backend"]
    task_ref["status"] = "ready_for_dev"

    bug = {
        "id": "BUG-1",
        "type": "bug",
        "title": "Ошибка при запуске validate",
        "description": "Команда validate падает на некорректных входных данных.",
        "status": "idea",
        "priority": "medium",
        "severity": "medium",
        "depends_on": [],
        "blocked_by": [],
        "blocked_reason": "",
        "tags": ["demo", "bug"],
        "estimate": None,
        "related_decisions": [],
        "release_id": None,
        "artifacts": {
            "analysis": None,
            "acceptance_criteria": [],
            "architecture": None,
            "technical_risks": [],
            "implementation_guidance": None,
            "implementation": None,
            "implementation_plan": {
                "summary": "",
                "files_to_create": [],
                "files_to_modify": [],
                "proposed_changes": [],
                "commands_to_run": [],
                "tests_to_add": [],
                "risks": [],
                "rollback_notes": "",
            },
            "patch_proposal": {
                "summary": "",
                "files": [],
                "unified_diff": "",
                "requires_approval": True,
                "approved": False,
                "applied": False,
                "applied_at": None,
            },
            "changed_files": [],
            "developer_notes": None,
            "test_cases": [],
            "edge_cases": [],
            "bugs": [],
            "qa_verification": {
                "verdict": "unknown",
                "summary": "",
                "checked_items": [],
                "failed_checks": [],
                "bugs_found": [],
                "recommended_next_status": "review",
            },
            "command_results": [],
            "repository_context": {
                "attached": False,
                "scanned_at": None,
                "repo_root": ".",
                "summary": {"total_files_indexed": 0, "interesting_directories": [], "ignored_paths": []},
                "relevant_files": [],
                "search_hits": [],
            },
            "bug_report": {
                "summary": "validate падает на части задач.",
                "environment": "local",
                "steps_to_reproduce": ["Запустить python3 run.py validate", "Наблюдать ошибку"],
                "actual_result": "Падает с ошибкой валидации.",
                "expected_result": "Показывает детализированную ошибку без падения процесса.",
                "logs": [],
                "attachments": [],
                "suspected_area": "orchestrator validation",
                "impact": "demo blocker",
            },
        },
        "history": [],
    }
    tasks.append(bug)
    orchestrator.save_tasks(tasks)

    metadata = create_decision(
        title="Использовать JSON-хранилище на MVP",
        context="Нужна простая локальная схема хранения без БД.",
        decision="Хранить данные в JSON-файлах до следующего этапа.",
        consequences="Просто запускать локально, но ограниченная масштабируемость.",
        status="accepted",
        tags=["mvp", "storage"],
        related_tasks=[task["id"]],
    )

    tasks = orchestrator.load_tasks()
    link_decision_to_task(tasks, task["id"], metadata["id"])
    orchestrator.save_tasks(tasks)

    release = create_release("v0.1.0-demo", "Demo release package")
    tasks = orchestrator.load_tasks()
    releases = load_releases()
    add_task_to_release(tasks, releases, task["id"], release["id"])
    add_task_to_release(tasks, releases, "BUG-1", release["id"])
    orchestrator.save_tasks(tasks)
    save_releases(releases)
    validate_all_tasks()
    print(f"Demo-данные созданы: {task['id']}, BUG-1, {metadata['id']}, {release['id']}")


def cmd_demo(_args):
    lines = [
        "Рекомендуемый demo flow:",
        "1. python3 run.py backlog",
        "2. python3 run.py next-task",
        "3. python3 run.py attach-repo-context --id TASK-1",
        "4. python3 run.py run-next --id TASK-1",
        "5. python3 run.py dev-plan --id TASK-1",
        "6. python3 run.py run-command --id TASK-1 --command \"python3 run.py validate\"",
        "7. python3 run.py run-next --id TASK-1",
        "8. python3 run.py qa-report --id TASK-1",
        "9. python3 run.py release-readiness --id REL-001",
        "10. python3 run.py release-notes --id REL-001",
    ]
    print("\n".join(lines))


def _find_or_create_demo_task() -> str:
    tasks = orchestrator.load_tasks()
    existing = next((item for item in tasks if item.get("title") == "DEMO: Добавить healthcheck-команду"), None)
    if existing is not None:
        return existing["id"]
    task = create_task("DEMO: Добавить healthcheck-команду", "E2E demo task for local MVP validation.")
    return task["id"]


def cmd_e2e_demo(_args):
    fake_client = get_llm_client("fake")
    task_id = _find_or_create_demo_task()

    tasks, task = _load_task_for_update(task_id)
    context = build_repository_context_for_task(task)
    task["artifacts"]["repository_context"] = context
    add_history_event(task, "Attached repository context in e2e-demo.", agent="orchestrator")
    orchestrator.save_tasks(tasks)

    statuses = []
    for _ in range(3):
        current = get_task(task_id)
        if current is None:
            break
        if current["status"] in {"in_progress", "review", "done"}:
            break
        updated, message = run_next_for_task(task_id, llm_client=fake_client)
        statuses.append(f"{updated['status']}: {message}")

    result_validate = run_safe_command("python3 run.py validate", cwd=None, timeout_seconds=30)
    result_tests = run_safe_command("python3 -m unittest discover -s tests", cwd=None, timeout_seconds=120)
    tasks, task = _load_task_for_update(task_id)
    result_validate["source"] = "e2e_demo"
    result_tests["source"] = "e2e_demo"
    task["artifacts"]["command_results"].append(result_validate)
    task["artifacts"]["command_results"].append(result_tests)
    add_history_event(task, "Recorded e2e-demo command results.", agent="orchestrator")
    orchestrator.save_tasks(tasks)

    current = get_task(task_id)
    if current and current["status"] == "in_progress":
        updated, message = run_next_for_task(task_id, llm_client=fake_client)
        statuses.append(f"{updated['status']}: {message}")

    releases = load_releases()
    rel = next((item for item in releases if item.get("name") == "v0.1.0-e2e-demo"), None)
    if rel is None:
        rel = create_release("v0.1.0-e2e-demo", "Auto-generated e2e demo release")
        releases = load_releases()
    tasks = orchestrator.load_tasks()
    add_task_to_release(tasks, releases, task_id, rel["id"])
    orchestrator.save_tasks(tasks)
    save_releases(releases)

    ready = calculate_release_readiness(tasks, rel)
    notes = generate_release_notes(tasks, rel)
    plan = get_task_implementation_plan(task_id) or {}
    qa = get_task(task_id)["artifacts"].get("qa_verification", {}) if get_task(task_id) else {}
    cmd_results = get_task(task_id)["artifacts"].get("command_results", []) if get_task(task_id) else []

    print("=== E2E Demo (fake provider) ===")
    print(f"Task: {task_id}")
    if statuses:
        print("Workflow transitions:")
        for line in statuses:
            print(f"- {line}")
    print(f"Developer plan summary: {plan.get('summary', '') or '(empty)'}")
    print(f"Command results recorded: {len(cmd_results)}")
    print(f"QA verdict: {qa.get('verdict', 'unknown')}")
    print(f"Release: {rel['id']} | ready={ready['ready']}")
    print("Release notes preview:")
    print("\n".join(notes.splitlines()[:12]))


def cmd_storage_info(_args):
    storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
    storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
    storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH
    storage.JSON_COLLECTION_PATHS["sessions"] = Path("sessions") / "sessions.json"
    info = storage.storage_info()
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_storage_init(_args):
    storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
    storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
    storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH
    storage.JSON_COLLECTION_PATHS["sessions"] = Path("sessions") / "sessions.json"
    storage.init_storage()
    print("Storage initialized.")


def cmd_migrate_json_to_sqlite(args):
    storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
    storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
    storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH
    storage.JSON_COLLECTION_PATHS["sessions"] = Path("sessions") / "sessions.json"
    result = storage.migrate_json_to_sqlite(overwrite=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_export_sqlite_to_json(args):
    storage.JSON_COLLECTION_PATHS["tasks"] = orchestrator.TASKS_PATH
    storage.JSON_COLLECTION_PATHS["releases"] = release_manager.RELEASES_PATH
    storage.JSON_COLLECTION_PATHS["decisions_index"] = decision_log.DECISION_INDEX_PATH
    storage.JSON_COLLECTION_PATHS["sessions"] = Path("sessions") / "sessions.json"
    result = storage.export_sqlite_to_json(overwrite=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


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


def format_project_status(result: dict) -> str:
    lines = [
        "Статус проекта:",
        result.get("message", ""),
        f"- total: {result.get('total_tasks', 0)}",
        f"- ready: {result.get('ready_tasks_count', 0)}",
        f"- blocked: {result.get('blocked_tasks_count', 0)}",
        f"- releases: {result.get('releases_count', 0)} (ready: {result.get('ready_releases_count', 0)})",
    ]
    next_task = (result.get("next_recommendation") or {}).get("next_task")
    if isinstance(next_task, dict):
        lines.append(f"- next: {next_task.get('id')} | {next_task.get('title')}")
    return "\n".join(lines)


def format_task_status(result: dict) -> str:
    art = result.get("artifact_summary", {})
    lines = [
        f"{result.get('id')} | {result.get('type')} | {result.get('status')} | {result.get('priority')}",
        result.get("title", ""),
        f"blocked={result.get('blocked')} reason={result.get('blocked_reason')}",
        f"depends_on={result.get('depends_on', [])}",
        f"release_id={result.get('release_id')}",
        f"related_decisions={result.get('related_decisions', [])}",
        (
            "artifacts: "
            f"analysis={art.get('has_analysis')} "
            f"architecture={art.get('has_architecture')} "
            f"plan={art.get('has_implementation_plan')} "
            f"patch={art.get('has_patch_proposal')} "
            f"commands={art.get('has_command_results')} "
            f"qa={art.get('qa_verdict')} "
            f"repo_ctx={art.get('repository_context_attached')}"
        ),
    ]
    return "\n".join(lines)


def cmd_project_status(_args):
    print(format_project_status(get_project_status()))


def cmd_task_status(args):
    print(format_task_status(get_task_status(args.id)))


def cmd_prepare_task(args):
    print(json.dumps(prepare_task_for_development(args.id), ensure_ascii=False, indent=2))


def cmd_advance_task(args):
    print(json.dumps(advance_task_safely(args.id, target_status=args.target), ensure_ascii=False, indent=2))


def cmd_next_work(_args):
    print(json.dumps(get_next_work_recommendation(), ensure_ascii=False, indent=2))


def cmd_blockers(_args):
    print(json.dumps(get_blockers_summary(), ensure_ascii=False, indent=2))


def cmd_add_note(args):
    note = add_task_note(args.id, args.text, author=args.author)
    print(json.dumps(note, ensure_ascii=False, indent=2))


def cmd_notes(args):
    print(json.dumps(list_task_notes(args.id), ensure_ascii=False, indent=2))


def cmd_task_discussion(args):
    print(json.dumps(summarize_task_discussion(args.id), ensure_ascii=False, indent=2))


def cmd_release_summary(args):
    print(json.dumps(get_release_summary(args.id), ensure_ascii=False, indent=2))


def cmd_repo_scan(_args):
    summary = scan_repository()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_repo_tree(args):
    entries = list_repository_tree(max_depth=args.depth)
    for item in entries:
        print(item)


def cmd_repo_file(args):
    preview = read_repository_file(path=args.path, max_chars=args.max_chars)
    print(json.dumps(preview, ensure_ascii=False, indent=2))


def cmd_repo_search(args):
    hits = search_repository(query=args.query, max_results=args.limit)
    if not hits:
        print("No matches found.")
        return
    for hit in hits:
        print(f"{hit['path']}:{hit['line_number']} | {hit['line']}")


def cmd_attach_repo_context(args):
    tasks, task = _load_task_for_update(args.id)
    context = build_repository_context_for_task(task)
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
    session_id = getattr(args, "session_id", None) or "cli:default"
    plan = plan_supervisor_action(args.text, session_id=session_id, user_id="cli-user", channel="cli")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        return
    if plan["action"]["name"] in RISKY_ACTIONS and not args.yes:
        refusal = {
            "executed": False,
            "action": plan["action"]["name"],
            "refusal_reason": f"Refusing risky supervisor action '{plan['action']['name']}' without --yes confirmation.",
        }
        print(json.dumps(refusal, ensure_ascii=False, indent=2))
        return
    result = execute_supervisor_action(
        plan,
        confirmed=args.yes,
        session_id=session_id,
        user_id="cli-user",
        channel="cli",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_focus(_args):
    print(json.dumps(get_focus("cli:default", user_id="cli-user", channel="cli"), ensure_ascii=False, indent=2))


def cmd_focus_task(args):
    print(json.dumps(set_active_task("cli:default", args.id, user_id="cli-user", channel="cli"), ensure_ascii=False, indent=2))


def cmd_focus_release(args):
    print(
        json.dumps(
            set_active_release("cli:default", args.id, user_id="cli-user", channel="cli"),
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_focus_decision(args):
    print(
        json.dumps(
            set_active_decision("cli:default", args.id, user_id="cli-user", channel="cli"),
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_clear_focus(_args):
    print(json.dumps(clear_focus("cli:default", user_id="cli-user", channel="cli"), ensure_ascii=False, indent=2))


def cmd_sessions(_args):
    rows = []
    for item in load_sessions():
        rows.append(
            {
                "session_id": item.get("session_id"),
                "channel": item.get("channel"),
                "user_id": item.get("user_id"),
                "active_task_id": item.get("active_task_id"),
                "active_release_id": item.get("active_release_id"),
                "active_decision_id": item.get("active_decision_id"),
                "last_updated_at": item.get("last_updated_at"),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_session(args):
    session = get_or_create_session(args.id, channel="cli")
    preview = dict(session)
    preview["recent_messages"] = (session.get("recent_messages") or [])[-10:]
    print(json.dumps(preview, ensure_ascii=False, indent=2))


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
    create_bug_parser.add_argument("--provider", choices=["fake", "openai", "claude_code"], required=False)
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
    supervise_parser.add_argument("--session-id", required=False, default="cli:default")
    supervise_parser.set_defaults(func=cmd_supervise)

    focus_parser = subparsers.add_parser("focus", help="Показать текущий активный фокус CLI-сессии")
    focus_parser.set_defaults(func=cmd_focus)

    focus_task_parser = subparsers.add_parser("focus-task", help="Установить фокус на задаче")
    focus_task_parser.add_argument("--id", required=True)
    focus_task_parser.set_defaults(func=cmd_focus_task)

    focus_release_parser = subparsers.add_parser("focus-release", help="Установить фокус на релизе")
    focus_release_parser.add_argument("--id", required=True)
    focus_release_parser.set_defaults(func=cmd_focus_release)

    focus_decision_parser = subparsers.add_parser("focus-decision", help="Установить фокус на решении")
    focus_decision_parser.add_argument("--id", required=True)
    focus_decision_parser.set_defaults(func=cmd_focus_decision)

    clear_focus_parser = subparsers.add_parser("clear-focus", help="Очистить активный фокус CLI-сессии")
    clear_focus_parser.set_defaults(func=cmd_clear_focus)

    sessions_parser = subparsers.add_parser("sessions", help="Список сессий контекста")
    sessions_parser.set_defaults(func=cmd_sessions)

    session_parser = subparsers.add_parser("session", help="Детали одной сессии")
    session_parser.add_argument("--id", required=True)
    session_parser.set_defaults(func=cmd_session)

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
    run_next_parser.add_argument("--provider", choices=["fake", "openai", "claude_code"], required=False)
    run_next_parser.set_defaults(func=cmd_run_next)

    run_all_parser = subparsers.add_parser("run-all", help="Run one step for all non-done tasks")
    run_all_parser.add_argument("--provider", choices=["fake", "openai", "claude_code"], required=False)
    run_all_parser.set_defaults(func=cmd_run_all)

    validate_parser = subparsers.add_parser("validate", help="Validate all tasks")
    validate_parser.set_defaults(func=cmd_validate)

    project_status_parser = subparsers.add_parser("project-status", help="Показать сводный статус проекта")
    project_status_parser.set_defaults(func=cmd_project_status)

    task_status_parser = subparsers.add_parser("task-status", help="Показать детальный статус задачи")
    task_status_parser.add_argument("--id", required=True)
    task_status_parser.set_defaults(func=cmd_task_status)

    prepare_task_parser = subparsers.add_parser("prepare-task", help="Подготовить задачу до ready_for_dev")
    prepare_task_parser.add_argument("--id", required=True)
    prepare_task_parser.set_defaults(func=cmd_prepare_task)

    advance_task_parser = subparsers.add_parser("advance-task", help="Продвинуть задачу безопасно")
    advance_task_parser.add_argument("--id", required=True)
    advance_task_parser.add_argument("--target", required=False)
    advance_task_parser.set_defaults(func=cmd_advance_task)

    next_work_parser = subparsers.add_parser("next-work", help="Показать следующую рекомендуемую работу")
    next_work_parser.set_defaults(func=cmd_next_work)

    blockers_parser = subparsers.add_parser("blockers", help="Показать сводку блокеров")
    blockers_parser.set_defaults(func=cmd_blockers)

    add_note_parser = subparsers.add_parser("add-note", help="Добавить заметку к задаче")
    add_note_parser.add_argument("--id", required=True)
    add_note_parser.add_argument("--text", required=True)
    add_note_parser.add_argument("--author", required=False, default="user")
    add_note_parser.set_defaults(func=cmd_add_note)

    notes_parser = subparsers.add_parser("notes", help="Показать заметки задачи")
    notes_parser.add_argument("--id", required=True)
    notes_parser.set_defaults(func=cmd_notes)

    task_discussion_parser = subparsers.add_parser("task-discussion", help="Сводка обсуждения задачи")
    task_discussion_parser.add_argument("--id", required=True)
    task_discussion_parser.set_defaults(func=cmd_task_discussion)

    release_summary_parser = subparsers.add_parser("release-summary", help="Краткий статус релиза")
    release_summary_parser.add_argument("--id", required=True)
    release_summary_parser.set_defaults(func=cmd_release_summary)

    agents_parser = subparsers.add_parser("agents", help="List available agent prompt files")
    agents_parser.set_defaults(func=cmd_agents)

    config_parser = subparsers.add_parser("config", help="Show effective LLM config")
    config_parser.set_defaults(func=cmd_config)

    managed_parser = subparsers.add_parser("managed-project", help="Show managed project info")
    managed_parser.set_defaults(func=cmd_managed_project)

    managed_check_parser = subparsers.add_parser("managed-project-check", help="Validate managed project path")
    managed_check_parser.set_defaults(func=cmd_managed_project_check)

    llm_smoke_parser = subparsers.add_parser("llm-smoke", help="Run simple LLM provider smoke check")
    llm_smoke_parser.add_argument("--prompt", required=True)
    llm_smoke_parser.set_defaults(func=cmd_llm_smoke)

    telegram_cfg_parser = subparsers.add_parser("telegram-config", help="Show Telegram bot config presence")
    telegram_cfg_parser.set_defaults(func=cmd_telegram_config)

    board_cfg_parser = subparsers.add_parser("board-config", help="Show Telegram Board diagnostics")
    board_cfg_parser.set_defaults(func=cmd_board_config)

    board_ping_parser = subparsers.add_parser("board-ping", help="Smoke-test Telegram Board topics")
    board_ping_parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Show what would be sent without calling Telegram API",
    )
    board_ping_parser.add_argument(
        "--topic", default=None, metavar="KEY",
        help="Ping a single topic by key (e.g. agent_log). Omit to ping all.",
    )
    board_ping_parser.set_defaults(func=cmd_board_ping)

    telegram_parser = subparsers.add_parser("telegram", help="Run Telegram bot in polling mode")
    telegram_parser.set_defaults(func=cmd_telegram)

    voice_cfg_parser = subparsers.add_parser("voice-config", help="Show voice/STT configuration")
    voice_cfg_parser.set_defaults(func=cmd_voice_config)

    transcribe_parser = subparsers.add_parser("transcribe-file", help="Convert and transcribe audio file")
    transcribe_parser.add_argument("--path", required=True)
    transcribe_parser.set_defaults(func=cmd_transcribe_file)

    doctor_parser = subparsers.add_parser("doctor", help="Проверка готовности MVP")
    doctor_parser.set_defaults(func=cmd_doctor)

    demo_reset_parser = subparsers.add_parser("demo-reset", help="Сброс demo-данных")
    demo_reset_parser.add_argument("--yes", action="store_true")
    demo_reset_parser.set_defaults(func=cmd_demo_reset)

    demo_seed_parser = subparsers.add_parser("demo-seed", help="Создать demo-данные")
    demo_seed_parser.set_defaults(func=cmd_demo_seed)

    demo_parser = subparsers.add_parser("demo", help="Показать demo flow")
    demo_parser.set_defaults(func=cmd_demo)

    e2e_demo_parser = subparsers.add_parser("e2e-demo", help="Запустить e2e demo flow с fake provider")
    e2e_demo_parser.set_defaults(func=cmd_e2e_demo)

    storage_info_parser = subparsers.add_parser("storage-info", help="Показать storage backend и counts")
    storage_info_parser.set_defaults(func=cmd_storage_info)

    storage_init_parser = subparsers.add_parser("storage-init", help="Инициализировать storage backend")
    storage_init_parser.set_defaults(func=cmd_storage_init)

    migrate_storage_parser = subparsers.add_parser("migrate-json-to-sqlite", help="Миграция JSON -> SQLite")
    migrate_storage_parser.add_argument("--force", action="store_true")
    migrate_storage_parser.set_defaults(func=cmd_migrate_json_to_sqlite)

    export_storage_parser = subparsers.add_parser("export-sqlite-to-json", help="Экспорт SQLite -> JSON")
    export_storage_parser.add_argument("--force", action="store_true")
    export_storage_parser.set_defaults(func=cmd_export_sqlite_to_json)

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
    from env_loader import load_dotenv_if_exists
    load_dotenv_if_exists()
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except (ValueError, OSError, LLMClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
