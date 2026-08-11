"""The project status state machine — the single place a status transition is
considered valid or not. `services/projects/service.py` is the only caller;
nothing else (and certainly no LLM tool) mutates `ProjectModel.status`
directly, per the "Claude must not directly change project state" requirement.

Matches the lifecycle docs/PROJECT_GOVERNANCE.md diagrams, plus one practical
addition beyond the literal spec diagram: `rejected -> draft`, so a rejected
project isn't a permanent dead end — the PM edits the draft and resubmits
through the normal draft -> submitted path, rather than needing a second,
parallel "resubmit" transition.
"""

from app.core.errors import AppError

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"submitted"},
    "submitted": {"approved", "rejected"},
    "approved": {"active"},
    "active": {"paused", "completed", "cancelled"},
    "paused": {"active", "cancelled"},
    "completed": {"closed"},
    "closed": set(),
    "rejected": {"draft"},
    "cancelled": set(),
}


def transition(project, new_status: str) -> None:
    """Raises AppError(409) on an illegal move; otherwise sets
    `project.status` in place. Caller commits."""
    allowed = VALID_TRANSITIONS.get(project.status, set())
    if new_status not in allowed:
        raise AppError(
            409, "invalid_transition",
            f"Cannot move project from '{project.status}' to '{new_status}' "
            f"(valid next states: {sorted(allowed) or 'none — terminal state'})",
        )
    project.status = new_status
