import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.request_context import get_current_request_id
from app.db.postgres import get_db
from app.models.guardrail_policy import GUARDRAIL_POLICY_CATEGORIES, GuardrailPolicyModel, GuardrailPolicyVersionModel
from app.models.user import UserModel
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.auth.rbac import require_permission
from app.services.guardrail_policy import playground, service

# Every route in this router requires MANAGE_GUARDRAIL_POLICIES (Admin+CEO
# only per llm_rbac.yaml — see core/permissions.py's comment on this
# permission for why it's dedicated rather than reusing SYSTEM_SETTINGS).
# No broader read-only audience this pass: the spec's own RBAC section says
# "Employees: No access" and doesn't ask for a read tier below Admin/CEO the
# way VIEW_AUDIT_LOGS/VIEW_ANALYTICS split reads from writes elsewhere in
# this app.
router = APIRouter(
    tags=["guardrail-policies"], dependencies=[Depends(require_permission(Permission.MANAGE_GUARDRAIL_POLICIES))],
)

_MAX_LIMIT = 200


class GuardrailPolicyResponse(BaseModel):
    id: uuid.UUID
    policy_key: str
    name: str
    description: str | None
    category: str
    enabled: bool
    action: str
    priority: int
    configuration: dict
    mode: str
    version: int
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None


class GuardrailPolicyListResponse(BaseModel):
    items: list[GuardrailPolicyResponse]
    total: int


class GuardrailPolicyCreateRequest(BaseModel):
    policy_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    category: str
    action: str
    priority: int = Field(default=100, ge=1, le=1000)
    configuration: dict
    mode: Literal["ENFORCE", "DRY_RUN"] = "ENFORCE"


class GuardrailPolicyUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    action: str | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    configuration: dict | None = None
    mode: Literal["ENFORCE", "DRY_RUN"] | None = None
    reason: str | None = None


class GuardrailPolicyUpdateResponse(BaseModel):
    status: Literal["applied", "pending_approval"]
    policy: GuardrailPolicyResponse | None = None
    approval_id: uuid.UUID | None = None


class RollbackRequest(BaseModel):
    expected_version: int = Field(ge=1)
    target_version: int = Field(ge=1)


class PolicyVersionResponse(BaseModel):
    version: int
    changed_by: uuid.UUID | None
    previous_configuration: dict | None
    new_configuration: dict
    reason: str | None
    changed_at: datetime


class PolicyTestRequest(BaseModel):
    category: str
    configuration: dict
    action: str = "BLOCK"
    sample_text: str = Field(min_length=1, max_length=4000)
    # PII-only: which of the proposed configuration's independent
    # input_action/output_action to resolve against (spec §15's separate
    # INPUT TEST / OUTPUT TEST). Ignored by every other category.
    direction: Literal["input", "output"] | None = None


class PolicyTestResponse(BaseModel):
    category: str
    detected: bool
    action: str
    risk_level: str
    detail: str


def _to_response(p: GuardrailPolicyModel) -> GuardrailPolicyResponse:
    return GuardrailPolicyResponse(
        id=p.id, policy_key=p.policy_key, name=p.name, description=p.description, category=p.category,
        enabled=p.enabled, action=p.action, priority=p.priority, configuration=p.configuration, mode=p.mode,
        version=p.version, created_by=p.created_by, updated_by=p.updated_by,
        created_at=p.created_at, updated_at=p.updated_at,
    )


@router.get("/guardrail-policies", response_model=GuardrailPolicyListResponse)
def list_policies(
    category: str | None = None, enabled: bool | None = None, limit: int = 50, offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = min(limit, _MAX_LIMIT)
    query = db.query(GuardrailPolicyModel)
    if category is not None:
        query = query.filter(GuardrailPolicyModel.category == category)
    if enabled is not None:
        query = query.filter(GuardrailPolicyModel.enabled == enabled)
    total = query.count()
    rows = query.order_by(GuardrailPolicyModel.category, GuardrailPolicyModel.priority).offset(offset).limit(limit).all()
    return GuardrailPolicyListResponse(items=[_to_response(r) for r in rows], total=total)


@router.get("/guardrail-policies/{policy_id}", response_model=GuardrailPolicyResponse)
def get_policy(policy_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(GuardrailPolicyModel, policy_id)
    if row is None:
        raise AppError(404, "policy_not_found", f"Guardrail policy {policy_id} not found")
    return _to_response(row)


@router.get("/guardrail-policies/{policy_id}/versions", response_model=list[PolicyVersionResponse])
def list_policy_versions(policy_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.get(GuardrailPolicyModel, policy_id) is None:
        raise AppError(404, "policy_not_found", f"Guardrail policy {policy_id} not found")
    rows = (
        db.query(GuardrailPolicyVersionModel)
        .filter(GuardrailPolicyVersionModel.policy_id == policy_id)
        .order_by(GuardrailPolicyVersionModel.version.desc())
        .all()
    )
    return [
        PolicyVersionResponse(
            version=r.version, changed_by=r.changed_by, previous_configuration=r.previous_configuration,
            new_configuration=r.new_configuration, reason=r.reason, changed_at=r.changed_at,
        )
        for r in rows
    ]


@router.post("/guardrail-policies", response_model=GuardrailPolicyResponse, status_code=201)
def create_policy(
    body: GuardrailPolicyCreateRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission(Permission.MANAGE_GUARDRAIL_POLICIES)),
):
    if body.category not in GUARDRAIL_POLICY_CATEGORIES:
        raise AppError(422, "invalid_category", f"category must be one of {GUARDRAIL_POLICY_CATEGORIES}")
    result = service.create_policy(
        db, policy_key=body.policy_key, name=body.name, description=body.description, category=body.category,
        action=body.action, priority=body.priority, configuration=body.configuration, mode=body.mode,
        created_by=current_user,
    )
    if result.approval is not None:
        # SF-09: creating a row weaker than the safe default for a critical
        # entity is a weakening, and is queued rather than applied — the same
        # treatment editing one already received.
        raise AppError(
            202, "approval_required",
            "This would weaken protection for a critical PII entity, so it has been queued for "
            "approval rather than applied. See the Approvals tab.",
        )
    return _to_response(result.policy)


@router.patch("/guardrail-policies/{policy_id}", response_model=GuardrailPolicyUpdateResponse)
def update_policy(
    policy_id: uuid.UUID, body: GuardrailPolicyUpdateRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission(Permission.MANAGE_GUARDRAIL_POLICIES)),
):
    updates = body.model_dump(exclude={"expected_version", "reason"}, exclude_none=True)
    result = service.update_policy(
        db, policy_id, expected_version=body.expected_version, updates=updates, updated_by=current_user,
        reason=body.reason,
    )
    if result.policy is not None:
        return GuardrailPolicyUpdateResponse(status="applied", policy=_to_response(result.policy))
    return GuardrailPolicyUpdateResponse(status="pending_approval", approval_id=result.approval.id)


@router.post("/guardrail-policies/{policy_id}/rollback", response_model=GuardrailPolicyResponse)
def rollback_policy(
    policy_id: uuid.UUID, body: RollbackRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission(Permission.MANAGE_GUARDRAIL_POLICIES)),
):
    policy = service.rollback_policy(
        db, policy_id, expected_version=body.expected_version, target_version=body.target_version,
        changed_by=current_user,
    )
    return _to_response(policy)


@router.post("/guardrail-policies/test", response_model=PolicyTestResponse)
def test_policy(
    body: PolicyTestRequest,
    current_user: UserModel = Depends(require_permission(Permission.MANAGE_GUARDRAIL_POLICIES)),
):
    if body.category not in GUARDRAIL_POLICY_CATEGORIES:
        raise AppError(422, "invalid_category", f"category must be one of {GUARDRAIL_POLICY_CATEGORIES}")
    result = playground.evaluate(body.category, body.configuration, body.action, body.sample_text, body.direction)
    # Never the sample text itself (may contain real PII an operator typed
    # in to test a rule) — only the category and outcome, same "labels only,
    # never values" rule every other guardrail audit surface in this app
    # follows. See services/audit/logger.py's own metadata allowlist.
    audit_logger.log(
        AuditEventType.POLICY_TESTED, outcome=AuditOutcome.SUCCESS, request_id=get_current_request_id(),
        actor_id=current_user.id, actor_role=current_user.role, resource_type="GUARDRAIL_POLICY",
        action="TEST", metadata={"guardrail_category": body.category},
    )
    return PolicyTestResponse(**result)
