import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.roles import Role
from app.db.postgres import get_db
from app.gateway.usage_tracker import record_denied
from app.models.project import ProjectModel
from app.models.project_member import ProjectMemberModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_role
from app.services.llm_rbac.engine import authorize_llm_request
from app.services.projects import service as projects_service

router = APIRouter()

# CEO was split out from Admin (previously one combined "CEO/Admin" role) —
# every place below that treated Role.ADMIN as "the executive tier" now
# treats this set the same way, so CEO keeps the oversight/approval powers
# it always conceptually had.
_EXECUTIVE_ROLES = {Role.ADMIN.value, Role.CEO.value}


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None
    department: str | None = None
    manager_id: uuid.UUID | None = None  # Admin/CEO only — PM is always their own manager


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    department: str | None = None


class MembersRequest(BaseModel):
    user_ids: list[uuid.UUID]
    role_on_project: str | None = None


class PriorityRequest(BaseModel):
    priority: str


class ManagerRequest(BaseModel):
    manager_id: uuid.UUID


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    department: str | None
    manager_id: uuid.UUID | None
    created_by: uuid.UUID | None
    priority: str
    status: str
    created_at: datetime
    updated_at: datetime | None
    closed_at: datetime | None


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int


class MemberResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role_on_project: str | None
    assigned_at: datetime


def _to_response(p: ProjectModel) -> ProjectResponse:
    return ProjectResponse(
        id=p.id, name=p.name, description=p.description, department=p.department,
        manager_id=p.manager_id, created_by=p.created_by, priority=p.priority, status=p.status,
        created_at=p.created_at, updated_at=p.updated_at, closed_at=p.closed_at,
    )


def _authorize(db: Session, current_user: UserModel, action: str):
    try:
        return authorize_llm_request(db, current_user, endpoint="projects", action=action)
    except AppError as exc:
        record_denied(
            agent_name="projects_endpoint", user_id=current_user.id, role=current_user.role,
            department=current_user.department, denial_reason=str(exc.detail), requested_capability=action,
        )
        raise


def _visible_project_ids(db: Session, current_user: UserModel):
    """CEO/Admin see every project. A Project Manager sees projects they
    manage or are a member of — everyone else sees none (Employee/HR have no
    project-governance role at all, per spec §2-3)."""
    if current_user.role in _EXECUTIVE_ROLES:
        return None  # None = unrestricted, same sentinel convention as knowledge_departments_for
    if current_user.role != Role.PROJECT_MANAGER.value:
        return set()
    member_of = {
        r[0] for r in db.query(ProjectMemberModel.project_id)
        .filter(ProjectMemberModel.user_id == current_user.id).all()
    }
    owned = {r[0] for r in db.query(ProjectModel.id).filter(ProjectModel.manager_id == current_user.id).all()}
    return owned | member_of


def _get_visible_project(db: Session, project_id: uuid.UUID, current_user: UserModel) -> ProjectModel:
    project = db.get(ProjectModel, project_id)
    if project is None:
        raise AppError(404, "project_not_found", f"Project {project_id} not found")
    visible_ids = _visible_project_ids(db, current_user)
    if visible_ids is not None and project.id not in visible_ids:
        raise AppError(404, "project_not_found", f"Project {project_id} not found")
    return project


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    body: ProjectCreateRequest, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
):
    _authorize(db, current_user, "project_creation")
    manager_id = current_user.id
    if current_user.role in _EXECUTIVE_ROLES and body.manager_id is not None:
        manager_id = body.manager_id
    project = projects_service.create_project(
        db, name=body.name, description=body.description, department=body.department,
        manager_id=manager_id, created_by=current_user.id,
    )
    return _to_response(project)


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    visible_ids = _visible_project_ids(db, current_user)
    if visible_ids is not None and not visible_ids:
        return ProjectListResponse(items=[], total=0)
    query = db.query(ProjectModel)
    if visible_ids is not None:
        query = query.filter(ProjectModel.id.in_(visible_ids))
    query = query.order_by(ProjectModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return ProjectListResponse(items=[_to_response(r) for r in rows], total=total)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
):
    return _to_response(_get_visible_project(db, project_id, current_user))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID, body: ProjectUpdateRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    _authorize(db, current_user, "project_update")
    project = _get_visible_project(db, project_id, current_user)
    is_executive = current_user.role in _EXECUTIVE_ROLES
    if not is_executive and project.manager_id != current_user.id:
        raise AppError(403, "not_project_manager", "Only the assigned Project Manager may update this project")
    updated = projects_service.update_project(
        db, project, name=body.name, description=body.description, department=body.department,
        allow_any_status=is_executive,
    )
    return _to_response(updated)


@router.post("/projects/{project_id}/members", response_model=list[MemberResponse], status_code=201)
def add_members(
    project_id: uuid.UUID, body: MembersRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    _authorize(db, current_user, "project_allocation")
    project = _get_visible_project(db, project_id, current_user)
    is_executive = current_user.role in _EXECUTIVE_ROLES
    if not is_executive and project.manager_id != current_user.id:
        raise AppError(403, "not_project_manager", "Only the assigned Project Manager may allocate members")
    rows = projects_service.add_members(db, project, body.user_ids, role_on_project=body.role_on_project)
    return [
        MemberResponse(
            id=r.id, project_id=r.project_id, user_id=r.user_id,
            role_on_project=r.role_on_project, assigned_at=r.assigned_at,
        )
        for r in rows
    ]


class ApprovalRequestResponse(BaseModel):
    id: uuid.UUID
    action: str
    target_type: str
    target_id: uuid.UUID
    status: str
    created_at: datetime


@router.post("/projects/{project_id}/submit", response_model=ApprovalRequestResponse, status_code=201)
def submit_project(
    project_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
):
    _authorize(db, current_user, "project_submit")
    project = _get_visible_project(db, project_id, current_user)
    if current_user.role not in _EXECUTIVE_ROLES and project.manager_id != current_user.id:
        raise AppError(403, "not_project_manager", "Only the assigned Project Manager may submit this project")
    approval = projects_service.submit_for_approval(db, project, current_user)
    return ApprovalRequestResponse(
        id=approval.id, action=approval.action, target_type=approval.target_type,
        target_id=approval.target_id, status=approval.status, created_at=approval.created_at,
    )


# ------------------------------------------------------- CEO/Admin-only direct actions
# No approval step for any of these — Admin/CEO is already the final
# authority, and a Project Manager has no path to reach any of them at all
# (require_role rejects with 403 before the handler body runs), matching
# spec §4's explicit "Project Manager cannot: Close/Cancel/Change Project
# Manager/..." list. See docs/PROJECT_GOVERNANCE.md for the full reasoning.

@router.post("/projects/{project_id}/reallocate", response_model=list[MemberResponse])
def reallocate_project(
    project_id: uuid.UUID, body: MembersRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    project = db.get(ProjectModel, project_id)
    if project is None:
        raise AppError(404, "project_not_found", f"Project {project_id} not found")
    rows = projects_service.reallocate_members(db, project, body.user_ids)
    return [
        MemberResponse(
            id=r.id, project_id=r.project_id, user_id=r.user_id,
            role_on_project=r.role_on_project, assigned_at=r.assigned_at,
        )
        for r in rows
    ]


@router.post("/projects/{project_id}/priority", response_model=ProjectResponse)
def change_priority(
    project_id: uuid.UUID, body: PriorityRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    project = db.get(ProjectModel, project_id)
    if project is None:
        raise AppError(404, "project_not_found", f"Project {project_id} not found")
    if body.priority not in ("low", "medium", "high", "critical"):
        raise AppError(422, "invalid_priority", "priority must be one of low/medium/high/critical")
    return _to_response(projects_service.change_priority(db, project, body.priority))


@router.post("/projects/{project_id}/manager", response_model=ProjectResponse)
def change_manager(
    project_id: uuid.UUID, body: ManagerRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    project = db.get(ProjectModel, project_id)
    if project is None:
        raise AppError(404, "project_not_found", f"Project {project_id} not found")
    return _to_response(projects_service.change_manager(db, project, body.manager_id))


def _direct_transition(project_id: uuid.UUID, db: Session, fn):
    project = db.get(ProjectModel, project_id)
    if project is None:
        raise AppError(404, "project_not_found", f"Project {project_id} not found")
    return _to_response(fn(db, project))


@router.post("/projects/{project_id}/pause", response_model=ProjectResponse)
def pause_project(
    project_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    return _direct_transition(project_id, db, projects_service.pause)


@router.post("/projects/{project_id}/resume", response_model=ProjectResponse)
def resume_project(
    project_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    return _direct_transition(project_id, db, projects_service.resume)


@router.post("/projects/{project_id}/complete", response_model=ProjectResponse)
def complete_project(
    project_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    # Not one of spec §5's named CEO actions, but structurally required to
    # ever reach `closed` per the lifecycle diagram in spec §14 (`close()`
    # only accepts a `completed` project) — see docs/PROJECT_GOVERNANCE.md.
    return _direct_transition(project_id, db, projects_service.complete)


@router.post("/projects/{project_id}/close", response_model=ProjectResponse)
def close_project(
    project_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    return _direct_transition(project_id, db, projects_service.close)


@router.post("/projects/{project_id}/cancel", response_model=ProjectResponse)
def cancel_project(
    project_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    return _direct_transition(project_id, db, projects_service.cancel)
