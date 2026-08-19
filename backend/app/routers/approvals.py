import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.roles import Role
from app.db.postgres import get_db
from app.models.approval_request import ApprovalRequestModel
from app.models.employee_pii_record import EmployeePIIRecordModel
from app.models.project import ProjectModel
from app.models.user import UserModel
from app.routers.documents import delete_document_row
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_role
from app.services.employee_pii import service as employee_pii_service
from app.services.guardrail_policy import service as guardrail_policy_service
from app.services.projects import service as projects_service

router = APIRouter()

# Was require_role(ADMIN) only — widened to include CEO now that it's a real,
# separate role: approvals are executive decisions (CEO/Admin sign off on a
# PM's/HR's queued document delete, a project submission, ...), and CEO
# should be able to make them same as Admin.
#
# HR was added on top of that (list/decide only — see _hr_employee_pii_scope()
# below) specifically for target_type="employee_pii" (docs/
# GUARDRAILS_ARCHITECTURE.md §14): HR may act on those, scoped to their own
# department, but project/document approvals stay exactly as Admin/CEO-only
# as before — require_role() alone can't express "this role, but only for
# this target_type," so that distinction is enforced in-handler below, not
# by the dependency.


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    action: str
    target_type: str
    target_id: uuid.UUID
    requested_by: uuid.UUID | None
    # Resolved alongside requested_by/decided_by (not a frontend-side lookup)
    # so "who requested this, who approved/rejected it" is answerable
    # directly from this response — a bare UUID satisfies the audit-trail
    # data model but not "clearly show ... by whom" for a human reading the
    # approvals page. None if the user row no longer exists (FK is
    # ON DELETE SET NULL) or the request/decision hasn't happened yet.
    requested_by_email: str | None = None
    role: str | None
    status: str
    decided_by: uuid.UUID | None
    decided_by_email: str | None = None
    decided_at: datetime | None
    reason: str | None
    created_at: datetime
    # Only populated on the single-item GET, and only once the caller has
    # already passed _can_view_approval()'s scope check below — never on the
    # list endpoint, so a list response can never leak another employee's
    # raw_message/result to a viewer who merely CAN see that a request
    # exists. See employee_pii/service.py's own docstring for what lives in
    # here (masked_message always; raw_message/result only for someone
    # authorized to see this specific request).
    payload: dict | None = None


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
    total: int


class DecideRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    # Only meaningful for target_type="employee_pii" write actions
    # (add/modify/store) — the decider's explicit, confirmed field value(s)
    # to write, read off payload.raw_message at decide time. Deliberately
    # not auto-parsed out of the original message (see
    # services/employee_pii/service.py's module docstring for why a human
    # confirming the exact value is the correct design here, not a
    # regex-guessed field mapping).
    values: dict[str, str] | None = None


def _resolve_emails(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Batched, not one query per row — list_approvals() collects every
    requested_by/decided_by across the whole page first and calls this once,
    so an N-row page costs one extra query, not up to 2N."""
    user_ids.discard(None)
    if not user_ids:
        return {}
    rows = db.query(UserModel.id, UserModel.email).filter(UserModel.id.in_(user_ids)).all()
    return {row.id: row.email for row in rows}


def _to_response(
    a: ApprovalRequestModel, *, include_payload: bool = False, emails: dict[uuid.UUID, str] | None = None,
) -> ApprovalResponse:
    emails = emails or {}
    return ApprovalResponse(
        id=a.id, action=a.action, target_type=a.target_type, target_id=a.target_id,
        requested_by=a.requested_by, requested_by_email=emails.get(a.requested_by), role=a.role, status=a.status,
        decided_by=a.decided_by, decided_by_email=emails.get(a.decided_by),
        decided_at=a.decided_at, reason=a.reason, created_at=a.created_at,
        payload=a.payload if include_payload else None,
    )


def _hr_employee_pii_scope_ok(user: UserModel, approval: ApprovalRequestModel, db: Session) -> bool:
    """True only for target_type="employee_pii" requests whose target
    record's department matches the HR user's own (or is unset — matching
    the same "no permission rows = unscoped" convention
    retrieval_permissions.py already uses elsewhere in this app). Admin/CEO
    never call this — they're unscoped by design (see module comment)."""
    if approval.target_type != "employee_pii":
        return False
    record = db.get(EmployeePIIRecordModel, approval.target_id)
    if record is None:
        return False
    return record.department is None or record.department == user.department


def _can_view_approval(user: UserModel, approval: ApprovalRequestModel, db: Session) -> bool:
    if user.role in (Role.ADMIN.value, Role.CEO.value):
        return True
    if approval.requested_by == user.id:
        # A requester may always check their own request's status/result —
        # they already know everything payload could reveal about their own
        # submission (they wrote raw_message; a granted read/retrieve's
        # result is literally what they asked for).
        return True
    if user.role == Role.HR.value:
        return _hr_employee_pii_scope_ok(user, approval, db)
    return False


@router.get("/approvals", response_model=ApprovalListResponse)
def list_approvals(
    status: str = "pending", limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO, Role.HR)),
):
    query = db.query(ApprovalRequestModel)
    if status:
        query = query.filter(ApprovalRequestModel.status == status)
    if current_user.role == Role.HR.value:
        # HR's list is scoped to employee_pii requests within their own
        # department only — never project/document approvals (that stays
        # Admin/CEO-only territory, unchanged) and never another
        # department's employee data.
        query = query.join(EmployeePIIRecordModel, ApprovalRequestModel.target_id == EmployeePIIRecordModel.id).filter(
            ApprovalRequestModel.target_type == "employee_pii",
            or_(
                EmployeePIIRecordModel.department.is_(None),
                EmployeePIIRecordModel.department == current_user.department,
            ),
        )
    query = query.order_by(ApprovalRequestModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    emails = _resolve_emails(db, {r.requested_by for r in rows} | {r.decided_by for r in rows})
    return ApprovalListResponse(items=[_to_response(r, emails=emails) for r in rows], total=total)


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
def get_approval(
    approval_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
):
    row = db.get(ApprovalRequestModel, approval_id)
    if row is None:
        raise AppError(404, "approval_not_found", f"Approval request {approval_id} not found")
    if not _can_view_approval(current_user, row, db):
        raise AppError(403, "insufficient_role", "You are not authorized to view this approval request")
    emails = _resolve_emails(db, {row.requested_by, row.decided_by})
    return _to_response(row, include_payload=True, emails=emails)


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalResponse)
def decide_approval(
    approval_id: uuid.UUID, body: DecideRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role(Role.ADMIN, Role.CEO, Role.HR)),
):
    """Generic across target_type — the one place a pending ApprovalRequestModel
    actually gets acted on. Dispatches to the domain's own service function
    rather than mutating target rows here directly, so this stays a thin
    orchestrator and the domain logic (project lifecycle, document deletion,
    employee PII) has exactly one owner each."""
    approval = db.get(ApprovalRequestModel, approval_id)
    if approval is None:
        raise AppError(404, "approval_not_found", f"Approval request {approval_id} not found")
    if approval.status != "pending":
        raise AppError(409, "already_decided", f"This request was already {approval.status}")

    if current_user.role == Role.HR.value and not _hr_employee_pii_scope_ok(current_user, approval, db):
        # Covers both cases HR must never decide: a non-employee_pii
        # approval (project/document — Admin/CEO-only, unchanged), and an
        # employee_pii request outside their own department scope. Not
        # treating "HR" as automatic permission over all employee PII per
        # the explicit security requirement this endpoint was built against.
        raise AppError(403, "insufficient_role", "This action requires one of: admin, ceo")

    if approval.target_type == "project":
        project = db.get(ProjectModel, approval.target_id)
        if project is None:
            raise AppError(404, "project_not_found", f"Project {approval.target_id} not found")
        projects_service.apply_decision(db, project, body.decision)
    elif approval.target_type == "document":
        if body.decision == "approved":
            delete_document_row(db, approval.target_id)
        # A rejected delete request just leaves the document untouched.
    elif approval.target_type == "employee_pii":
        employee_pii_service.apply_decision(db, approval, body.decision, current_user, body.values)
    elif approval.target_type == "guardrail_policy":
        guardrail_policy_service.apply_decision(db, approval, body.decision, current_user, body.values)
    else:
        raise AppError(422, "unsupported_target_type", f"No handler for target_type={approval.target_type!r}")

    approval.status = body.decision
    approval.decided_by = current_user.id
    approval.decided_at = datetime.now(timezone.utc)
    approval.reason = body.reason
    db.commit()
    db.refresh(approval)
    # decided_by is current_user by construction here — no lookup needed for
    # that one; requested_by still needs resolving (almost always a
    # different person than the decider).
    emails = _resolve_emails(db, {approval.requested_by})
    emails[current_user.id] = current_user.email
    return _to_response(approval, include_payload=True, emails=emails)
