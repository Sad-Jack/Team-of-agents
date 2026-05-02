import json
from pathlib import Path

from llm_client import LLMClientError, get_llm_client
from project_context_loader import load_project_context_text, list_project_context_files

AGENTS_DIR = Path("agents")
AGENT_FILES = {
    "analyst": "analyst.md",
    "architect": "architect.md",
    "developer": "developer.md",
    "qa": "qa.md",
    "bug_intake": "bug_intake.md",
}


def _load_agent_prompt(agent_name: str) -> tuple[str, str]:
    filename = AGENT_FILES.get(agent_name)
    if filename is None:
        raise ValueError(f"Unknown agent: {agent_name}")

    prompt_path = AGENTS_DIR / filename
    if not prompt_path.exists():
        raise ValueError(f"Agent prompt file is missing: {prompt_path.as_posix()}")

    return prompt_path.read_text(encoding="utf-8"), prompt_path.as_posix()


def _required_schema_for_agent(agent_name: str) -> dict:
    schemas = {
        "analyst": {
            "artifacts": {"analysis": "string", "acceptance_criteria": ["string"]},
            "message": "string",
        },
        "architect": {
            "artifacts": {
                "architecture": "string",
                "technical_risks": ["string"],
                "implementation_guidance": "string",
            },
            "message": "string",
        },
        "developer": {
            "artifacts": {
                "implementation": "string",
                "implementation_plan": {
                    "summary": "string",
                    "files_to_create": ["string"],
                    "files_to_modify": ["string"],
                    "proposed_changes": [
                        {
                            "file_path": "string",
                            "change_type": "create|modify|delete",
                            "reason": "string",
                            "description": "string",
                            "safe_to_apply": "boolean",
                        }
                    ],
                    "commands_to_run": ["string"],
                    "tests_to_add": ["string"],
                    "risks": ["string"],
                    "rollback_notes": "string",
                },
            },
            "message": "string",
        },
        "qa": {
            "artifacts": {
                "test_cases": ["string"],
                "edge_cases": ["string"],
                "bugs": ["string or object"],
            },
            "message": "string",
        },
        "bug_intake": {
            "artifacts": {
                "bug_report": {
                    "summary": "string",
                    "environment": "string",
                    "steps_to_reproduce": ["string"],
                    "actual_result": "string",
                    "expected_result": "string",
                    "logs": ["string"],
                    "attachments": ["string"],
                    "suspected_area": "string",
                    "impact": "string",
                }
            },
            "severity": "minor|major|critical|blocker|unknown",
            "priority": "low|medium|high|urgent",
            "message": "string",
        },
    }
    if agent_name not in schemas:
        raise ValueError(f"Unsupported agent schema: {agent_name}")
    return schemas[agent_name]


def run_agent(agent_name: str, task: dict, llm_client=None) -> dict:
    prompt, prompt_source = _load_agent_prompt(agent_name)
    project_context_text = load_project_context_text()
    context_files = list_project_context_files()
    client = llm_client or get_llm_client()

    common_instructions = "Return only valid JSON. Do not include markdown or explanations."
    if agent_name == "bug_intake":
        common_instructions = (
            "Return only valid JSON. Do not include markdown or explanations. "
            "Do not invent exact facts. Use 'unknown' when information is missing."
        )

    payload = {
        "agent_name": agent_name,
        "agent_prompt": prompt,
        "project_context": project_context_text,
        "task": task,
        "required_output_schema": _required_schema_for_agent(agent_name),
        "instructions": common_instructions,
    }

    raw_output = client.generate(payload)

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise LLMClientError(
            f"Agent '{agent_name}' returned invalid JSON: {exc}. Raw output: {raw_output}"
        ) from exc

    if not isinstance(parsed, dict):
        raise LLMClientError(f"Agent '{agent_name}' output must be a JSON object.")

    parsed.setdefault("artifacts", {})
    if not isinstance(parsed["artifacts"], dict):
        raise LLMClientError(f"Agent '{agent_name}' output field 'artifacts' must be an object.")

    parsed.setdefault("message", f"{agent_name} processed task.")
    parsed["prompt_source"] = prompt_source
    parsed["context_files_used"] = context_files
    return parsed
