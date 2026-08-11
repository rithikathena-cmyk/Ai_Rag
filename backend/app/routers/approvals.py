import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.roles import Role
from app.db.postgres import get_db
from app.models.approval_request import ApprovalRequestModel
from app.models.project import ProjectModel
from app.models.user import UserModel
from app.routers.documents import delete_document_row
from app.services.auth.rbac import require_role
from app.services.projects import service as projects_service

router = APIRouter()

# Was require_role(ADMIN) only — widened to include CEO now that it's a real,
# separate role: approvals are executive decisions (CEO/Admin sign off on a
# PM's/HR's queued document delete, a project submission, ...), and CEO
# should be able to make them same as Admin.


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    action: str
    target_type: str
    target_id: uuid.UUID
    requested_by: uuid.UUID | None
    role: str | None
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    reason: str | None
    created_at: datetime


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
    total: int


class DecideRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str | None = None


def _to_response(a: ApprovalRequestModel) -> ApprovalResponse:
    return ApprovalResponse(
        id=a.id, action=a.action, target_type=a.target_type, target_id=a.target_id,
        requested_by=a.requested_by, role=a.role, status=a.status,
        decided_by=a.decided_by, decided_at=a.decided_at, reason=a.reason, created_at=a.created_at,
    )


@router.get("/approvals", response_model=ApprovalListResponse)
def list_approvals(
    status: str = "pending", limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    query = db.query(ApprovalRequestModel)
    if status:
        query = query.filter(ApprovalRequestModel.status == status)
    query = query.order_by(ApprovalRequestModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return ApprovalListResponse(items=[_to_response(r) for r in rows], total=total)


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
def get_approval(
    approval_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    row = db.get(ApprovalRequestModel, approval_id)
    if row is None:
        raise AppError(404, "approval_not_found", f"Approval request {approval_id} not found")
    return _to_response(row)


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalResponse)
def decide_approval(
    approval_id: uuid.UUID, body: DecideRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO)),
):
    """Generic across target_type — the one place a pending ApprovalRequestModel
    actually gets acted on. Dispatches to the domain's own service function
    rather than mutating target rows here directly, so this stays a thin
    orchestrator and the domain logic (project lifecycle, document deletion)
    has exactly one owner each."""
    approval = db.get(ApprovalRequestModel, approval_id)
    if approval is None:
        raise AppError(404, "approval_not_found", f"Approval request {approval_id} not found")
    if approval.status != "pending":
        raise AppError(409, "already_decided", f"This request was already {approval.status}")

    if approval.target_type == "project":
        project = db.get(ProjectModel, approval.target_id)
        if project is None:
            raise AppError(404, "project_not_found", f"Project {approval.target_id} not found")
        projects_service.apply_decision(db, project, body.decision)
    elif approval.target_type == "document":
        if body.decision == "approved":
            delete_document_row(db, approval.target_id)
        # A rejected delete request just leaves the document untouched.
    else:
        raise AppError(422, "unsupported_target_type", f"No handler for target_type={approval.target_type!r}")

    approval.status = body.decision
    approval.decided_by = current_user.id
    approval.decided_at = datetime.now(timezone.utc)
    approval.reason = body.reason
    db.commit()
    db.refresh(approval)
    return _to_response(approval)
