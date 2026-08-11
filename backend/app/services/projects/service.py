"""Project CRUD + lifecycle service — pure backend business logic, no LLM
anywhere in this file. This is the *only* place `ProjectModel`/
`ProjectMemberModel` rows are written; routers/projects.py and
routers/approvals.py call into it, and services/agents/project_agent.py
(the planner's read-only tool) never imports it — the planner can only ever
read project data via that separate, ORM-scoped, read-only path, never
mutate it (see docs/PROJECT_GOVERNANCE.md)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.approval_request import ApprovalRequestModel
from app.models.project import ProjectModel
from app.models.project_member import ProjectMemberModel
from app.services.projects import lifecycle


def create_project(
    db: Session, *, name: str, description: str | None, department: str | None,
    manager_id: uuid.UUID | None, created_by: uuid.UUID,
) -> ProjectModel:
    project = ProjectModel(
        name=name, description=description, department=department,
        manager_id=manager_id, created_by=created_by, status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(
    db: Session, project: ProjectModel, *, name: str | None = None, description: str | None = None,
    department: str | None = None, allow_any_status: bool = False,
) -> ProjectModel:
    if project.status == "rejected":
        # Editing a rejected project is how a PM resubmits it — see
        # lifecycle.py's module docstring.
        lifecycle.transition(project, "draft")
    elif project.status != "draft" and not allow_any_status:
        raise AppError(409, "project_not_editable", "Only a draft project can be updated")

    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    if department is not None:
        project.department = department
    db.commit()
    db.refresh(project)
    return project


def add_members(
    db: Session, project: ProjectModel, user_ids: list[uuid.UUID], role_on_project: str | None = None,
) -> list[ProjectMemberModel]:
    existing = {
        r[0] for r in db.query(ProjectMemberModel.user_id).filter(ProjectMemberModel.project_id == project.id).all()
    }
    new_rows = [
        ProjectMemberModel(project_id=project.id, user_id=uid, role_on_project=role_on_project)
        for uid in user_ids if uid not in existing
    ]
    if new_rows:
        db.add_all(new_rows)
        db.commit()
    return (
        db.query(ProjectMemberModel).filter(ProjectMemberModel.project_id == project.id).all()
    )


def submit_for_approval(db: Session, project: ProjectModel, user) -> ApprovalRequestModel:
    lifecycle.transition(project, "submitted")
    approval = ApprovalRequestModel(
        action="project_submit", target_type="project", target_id=project.id,
        requested_by=user.id, role=user.role, status="pending",
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


def apply_decision(db: Session, project: ProjectModel, decision: str) -> None:
    """Called by routers/approvals.py once an ApprovalRequestModel targeting
    a project has been decided. `decision` is "approved" or "rejected" —
    validated by the caller against APPROVAL_STATUSES before this runs."""
    if decision == "approved":
        lifecycle.transition(project, "approved")
        lifecycle.transition(project, "active")  # auto-activate on approval
    else:
        lifecycle.transition(project, "rejected")
    db.commit()


# --------------------------------------------------- CEO/Admin direct actions
# Every function below is only ever reachable via routers/projects.py routes
# gated by require_role(Role.ADMIN) — no approval step, since Admin/CEO is
# already the final authority (see docs/PROJECT_GOVERNANCE.md's reasoning for
# why this differs from a PM's actions, which always route through approval).

def reallocate_members(db: Session, project: ProjectModel, user_ids: list[uuid.UUID]) -> list[ProjectMemberModel]:
    db.query(ProjectMemberModel).filter(ProjectMemberModel.project_id == project.id).delete(synchronize_session=False)
    rows = [ProjectMemberModel(project_id=project.id, user_id=uid) for uid in user_ids]
    if rows:
        db.add_all(rows)
    db.commit()
    return db.query(ProjectMemberModel).filter(ProjectMemberModel.project_id == project.id).all()


def change_priority(db: Session, project: ProjectModel, priority: str) -> ProjectModel:
    project.priority = priority
    db.commit()
    db.refresh(project)
    return project


def change_manager(db: Session, project: ProjectModel, manager_id: uuid.UUID) -> ProjectModel:
    project.manager_id = manager_id
    db.commit()
    db.refresh(project)
    return project


def pause(db: Session, project: ProjectModel) -> ProjectModel:
    lifecycle.transition(project, "paused")
    db.commit()
    db.refresh(project)
    return project


def resume(db: Session, project: ProjectModel) -> ProjectModel:
    lifecycle.transition(project, "active")
    db.commit()
    db.refresh(project)
    return project


def close(db: Session, project: ProjectModel) -> ProjectModel:
    if project.status != "completed":
        # Spec's lifecycle diagram only shows completed -> closed; a project
        # that's still active/paused must be completed or cancelled first.
        raise AppError(409, "invalid_transition", "Only a completed project can be closed")
    lifecycle.transition(project, "closed")
    project.closed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return project


def cancel(db: Session, project: ProjectModel) -> ProjectModel:
    lifecycle.transition(project, "cancelled")
    project.closed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return project


def complete(db: Session, project: ProjectModel) -> ProjectModel:
    lifecycle.transition(project, "completed")
    db.commit()
    db.refresh(project)
    return project
