from __future__ import annotations

from decision_log import read_decision_file


def load_decision_context_for_task(task: dict, max_decisions: int = 5) -> str:
    decision_ids = task.get("related_decisions", []) if isinstance(task, dict) else []
    if not isinstance(decision_ids, list) or not decision_ids:
        return ""

    blocks = []
    for decision_id in decision_ids[: max(0, int(max_decisions))]:
        if not isinstance(decision_id, str):
            continue
        content = read_decision_file(decision_id)
        blocks.append(f"# decision/{decision_id}\n{content}")
    return "\n\n".join(blocks).strip()
