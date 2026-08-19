import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.audit_event import AuditEventModel
from app.models.user import UserModel
from app.services.auth.rbac import require_permission

# Only GET routes exist in this router — no PUT/DELETE against audit_events
# anywhere in the app, which is what makes the trail append-only "by
# construction" rather than by a rule someone has to remember to enforce.
# Same permission the rest of the audit-log surface already uses
# (upload_logs.py, and the frontend's own /audit-logs route guard in
# App.tsx) — VIEW_AUDIT_LOGS is granted to CEO/Admin only in
# config/llm_rbac.yaml today, so every other role gets a clean 403 on this
# entire router, not a partial/scoped view. Per-role SCOPED visibility
# (e.g. "an Employee role sees only their own events") isn't implemented
# this pass — no other endpoint in this codebase has that concept either,
# and the spec's own wording hedges it ("if required").
router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_permission(Permission.VIEW_AUDIT_LOGS))])

_MAX_LIMIT = 200


class AuditEventResponse(BaseModel):
    event_id: uuid.UUID
    event_type: str
    actor_id: uuid.UUID | None
    actor_email: str | None
    actor_role: str | None
    resource_type: str | None
    resource_id: str | None
    action: str | None
    outcome: str
    reason_code: str | None
    request_id: str
    session_id: str | None
    metadata: dict
    created_at: datetime


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int


def _to_response(row: AuditEventModel, email_by_id: dict[uuid.UUID, str]) -> AuditEventResponse:
    return AuditEventResponse(
        event_id=row.event_id, event_type=row.event_type, actor_id=row.actor_id,
        actor_email=email_by_id.get(row.actor_id) if row.actor_id else None,
        actor_role=row.actor_role, resource_type=row.resource_type, resource_id=row.resource_id,
        action=row.action, outcome=row.outcome, reason_code=row.reason_code, request_id=row.request_id,
        session_id=row.session_id, metadata=row.metadata_ or {}, created_at=row.created_at,
    )


@router.get("/events", response_model=AuditEventListResponse)
def list_events(
    event_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    role: str | None = None,
    resource_type: str | None = None,
    outcome: str | None = None,
    request_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Parameterized filtering only — every filter below is a SQLAlchemy
    column comparison against a caller-supplied value, never string-built
    SQL, and `limit` is hard-capped server-side (_MAX_LIMIT) regardless of
    what a caller requests, so this can't become an unrestricted full-table
    scan no matter what query params are sent."""
    limit = min(limit, _MAX_LIMIT)

    query = db.query(AuditEventModel)
    if event_type is not None:
        query = query.filter(AuditEventModel.event_type == event_type)
    if actor_id is not None:
        query = query.filter(AuditEventModel.actor_id == actor_id)
    if role is not None:
        query = query.filter(AuditEventModel.actor_role == role)
    if resource_type is not None:
        query = query.filter(AuditEventModel.resource_type == resource_type)
    if outcome is not None:
        query = query.filter(AuditEventModel.outcome == outcome)
    if request_id is not None:
        query = query.filter(AuditEventModel.request_id == request_id)
    if date_from is not None:
        query = query.filter(AuditEventModel.created_at >= date_from)
    if date_to is not None:
        query = query.filter(AuditEventModel.created_at <= date_to)

    total = query.count()
    rows = query.order_by(AuditEventModel.created_at.desc()).offset(offset).limit(limit).all()

    actor_ids = {r.actor_id for r in rows if r.actor_id is not None}
    email_by_id = {}
    if actor_ids:
        email_by_id = {
            row.id: row.email for row in db.query(UserModel.id, UserModel.email).filter(UserModel.id.in_(actor_ids)).all()
        }

    return AuditEventListResponse(items=[_to_response(r, email_by_id) for r in rows], total=total)


@router.get("/events/{event_id}", response_model=AuditEventResponse)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.query(AuditEventModel).filter(AuditEventModel.event_id == event_id).one_or_none()
    if row is None:
        raise AppError(404, "audit_event_not_found", f"Audit event {event_id} not found")
    email_by_id = {}
    if row.actor_id is not None:
        user = db.get(UserModel, row.actor_id)
        if user is not None:
            email_by_id[user.id] = user.email
    return _to_response(row, email_by_id)
