"""Policy Copilot API — Admin/CEO only, enforced here.

Authorization is a router dependency, not a frontend concern: hiding the nav
item stops a user finding the page, it does not stop them calling the
endpoint. Every route below resolves the caller's real role server-side and
refuses anything short of the required permission.

The Copilot produces PROPOSALS. No route in this file writes policy —
applying an approved proposal goes through `guardrail_policy/service.py`,
which re-validates independently.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.approval_request import ApprovalRequestModel
from app.models.user import UserModel
from app.core.request_context import get_current_request_id
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.audit.logger import log as audit_log
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_permission
from app.services.policy_copilot import service
from app.services.policy_copilot.apply import apply_proposal, apply_regex_rule, apply_rollback, apply_word_rule
from app.services.policy_copilot.entities_view import list_active_policies
from app.services.policy_copilot.research.orchestrator import ResearchOrchestrator

router = APIRouter(prefix="/policy-copilot", tags=["policy-copilot"])


class CopilotChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("/chat", dependencies=[Depends(require_permission(Permission.POLICY_PROPOSE))])
def copilot_chat(
    body: CopilotChatRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Interpret one natural-language policy request.

    Gated on POLICY_PROPOSE rather than POLICY_READ because a single turn can
    create a proposal. Read-only inspection has its own endpoints below.
    """
    result = service.handle(body.message, user=current_user, db=db)

    # Audited whatever the outcome — a refused or invalid request is exactly
    # the kind of event worth being able to review later. The raw request is
    # recorded because it IS the security-relevant artifact; it is admin-typed
    # policy text, not user content, and must not contain PII.
    audit_log(
        AuditEventType.POLICY_CREATED if result.proposal_id else AuditEventType.POLICY_TESTED,
        outcome=AuditOutcome.SUCCESS if result.validation.valid else AuditOutcome.DENIED,
        request_id=get_current_request_id(),
        actor_id=current_user.id,
        actor_role=current_user.role,
        resource_type="POLICY",
        resource_id=str(result.proposal_id) if result.proposal_id else None,
        action=result.intent.intent.value,
        metadata={
            # Keys are allowlisted in services/audit/logger.py, and every
            # string here is scrubbed by redact_pii()+redact_secrets() before
            # it is written — an admin who pastes an example identifier does
            # not thereby write it to the audit log.
            "raw_request": result.intent.raw_request[:1000],
            "policy_intent": result.intent.intent.value,
            "detection_method": result.intent.method,
            "risk_level": result.risk,
            "proposal_id": str(result.proposal_id) if result.proposal_id else None,
            # Joined: the allowlist is primitives-only by design.
            "validation_errors": "; ".join(result.validation.errors) or None,
        },
    )
    return result.as_dict()


class ResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)


@router.post("/research", dependencies=[Depends(require_permission(Permission.POLICY_PROPOSE))])
def copilot_research(
    body: ResearchRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Research guardrail policies and gaps.

    Gated on POLICY_PROPOSE (same as /chat) because research can lead to
    proposals. Strictly GUARDRAIL_ONLY scope — rejects queries about
    documents, users, conversations, or any non-guardrail topics.

    Security model:
    - Scope classification is deterministic (regex-based)
    - Tool access is allowlist-only (read-only guardrail tools)
    - All operations are audit-logged
    - No external API access
    - All proposals require human approval before applying
    """
    orchestrator = ResearchOrchestrator(db)
    result = orchestrator.research(body.query)

    audit_log(
        AuditEventType.POLICY_RESEARCHED,
        outcome=AuditOutcome.SUCCESS if result.success else AuditOutcome.DENIED,
        request_id=get_current_request_id(),
        actor_id=current_user.id,
        actor_role=current_user.role,
        resource_type="POLICY_RESEARCH",
        action="research",
        metadata={
            "raw_query": body.query[:1000],
            "success": result.success,
            "message": result.message[:500] if result.message else None,
            "scope_type": result.scope.scope_type.value if result.scope else None,
            "scope_allowed": result.scope.is_allowed if result.scope else None,
            "intent_type": result.intent.intent.value if result.intent else None,
            "proposal_count": len(result.proposals) if result.proposals else 0,
        },
    )

    return {
        "success": result.success,
        "message": result.message,
        "scope": {
            "type": result.scope.scope_type.value if result.scope else None,
            "allowed": result.scope.is_allowed if result.scope else None,
            "reason": result.scope.reason if result.scope else None,
        } if result.scope else None,
        "intent": {
            "type": result.intent.intent.value if result.intent else None,
            "entity": result.intent.entity if result.intent else None,
            "focus_area": result.intent.focus_area if result.intent else None,
            "confidence": result.intent.confidence if result.intent else None,
        } if result.intent else None,
        "proposals": [
            {
                "entity": p.entity,
                "change_type": p.change_type,
                "description": p.description,
                "rationale": p.rationale,
                "impacts": p.impacts,
                "risks": p.risks,
            }
            for p in result.proposals
        ] if result.proposals else [],
        "trace": [
            {
                "phase": t.phase.value,
                "status": t.status,
                "detail": t.detail,
            }
            for t in result.trace
        ] if result.trace else [],
    }


@router.get("/policies", dependencies=[Depends(require_permission(Permission.POLICY_READ))])
def list_policies(db: Session = Depends(get_db)):
    """The active PII policy for every known entity, with its source and
    whether it is actually enforceable at runtime."""
    return {"policies": list_active_policies(db)}


@router.get("/proposals", dependencies=[Depends(require_permission(Permission.POLICY_READ))])
def list_proposals(db: Session = Depends(get_db), status: str | None = None):
    query = db.query(ApprovalRequestModel).filter(
        ApprovalRequestModel.target_type == service.PROPOSAL_TARGET_TYPE
    )
    if status:
        query = query.filter(ApprovalRequestModel.status == status)
    rows = query.order_by(ApprovalRequestModel.created_at.desc()).limit(100).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "action": r.action,
                "status": r.status,
                "role": r.role,
                "payload": r.payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            }
            for r in rows
        ]
    }


class DecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/proposals/{proposal_id}/approve", dependencies=[Depends(require_permission(Permission.POLICY_APPROVE))])
def approve_proposal(
    proposal_id: uuid.UUID,
    body: DecisionRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Approve a proposal and APPLY it.

    This is the only route in the Copilot that changes enforcement. It does
    not implement its own write logic — it calls the same
    `guardrail_policy/service.py` create/update functions the Policy Center
    uses, so there is exactly one way policy is written.

    Held to POLICY_APPROVE (CEO and Admin). Rejecting requires the same
    permission: deciding a proposal either way is one authority.
    """
    proposal = _load_pending(db, proposal_id)
    payload = proposal.payload or {}
    changes = payload.get("changes") or []

    if payload.get("intent") == "ROLLBACK_POLICY":
        entity = payload.get("entity")
        target_version = payload.get("target_version")
        if not entity or target_version is None:
            raise AppError(
                422, "proposal_missing_rollback_target",
                "This rollback proposal is missing its entity or target version.",
            )
        applied = [apply_rollback(
            db, entity=entity, target_version=target_version, approver=current_user, reason=body.reason,
        )]
    elif payload.get("intent") == "CREATE_WORD_RULE":
        word_rule = payload.get("word_rule")
        if not word_rule:
            raise AppError(
                422, "proposal_missing_word_rule",
                "This proposal records no concrete word rule and cannot be applied.",
            )
        applied = [apply_word_rule(db, word_rule=word_rule, approver=current_user, reason=body.reason)]
    elif payload.get("intent") == "CREATE_REGEX_RULE":
        regex_rule = payload.get("regex_rule")
        if not regex_rule or not regex_rule.get("pattern"):
            raise AppError(
                422, "proposal_missing_regex_rule",
                "This proposal records no concrete regex pattern and cannot be applied.",
            )
        applied = [apply_regex_rule(db, regex_rule=regex_rule, approver=current_user, reason=body.reason)]
    elif not changes:
        raise AppError(
            422, "proposal_has_no_changes",
            "This proposal records no concrete policy change and cannot be applied.",
        )
    else:
        applied = apply_proposal(
            db, changes=changes, approver=current_user, reason=body.reason,
            role_exceptions=payload.get("role_exceptions") or [],
        )

    proposal.status = "approved"
    proposal.decided_by = current_user.id
    proposal.reason = body.reason
    db.commit()

    audit_log(
        AuditEventType.POLICY_APPROVED,
        outcome=AuditOutcome.SUCCESS,
        request_id=get_current_request_id(),
        actor_id=current_user.id,
        actor_role=current_user.role,
        resource_type="POLICY",
        resource_id=str(proposal_id),
        action="approve",
        metadata={
            "risk_level": payload.get("risk"),
            # Exempted roles are named explicitly: "PHONE updated -> v3" alone
            # would not record that one role was granted full visibility,
            # which is the part of an approval most worth being able to review.
            "detail": "; ".join(
                [f"{a.entity} {a.operation} -> v{a.version}" for a in applied]
                + [
                    f"exempt {e['role']} on {e['location']} -> {e['action']}"
                    for e in (payload.get("role_exceptions") or [])
                ]
            ),
        },
    )
    return {
        "id": str(proposal_id),
        "status": "approved",
        "applied": [
            {
                "entity": a.entity, "policy_key": a.policy_key,
                "operation": a.operation, "version": a.version,
            }
            for a in applied
        ],
    }


@router.post("/proposals/{proposal_id}/reject", dependencies=[Depends(require_permission(Permission.POLICY_APPROVE))])
def reject_proposal(
    proposal_id: uuid.UUID,
    body: DecisionRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    proposal = _load_pending(db, proposal_id)
    proposal.status = "rejected"
    proposal.decided_by = current_user.id
    proposal.reason = body.reason
    db.commit()
    audit_log(
        AuditEventType.POLICY_REJECTED,
        outcome=AuditOutcome.SUCCESS,
        request_id=get_current_request_id(),
        actor_id=current_user.id,
        actor_role=current_user.role,
        resource_type="POLICY",
        resource_id=str(proposal_id),
        action="reject",
        metadata={"detail": body.reason},
    )
    return {"id": str(proposal_id), "status": "rejected"}


def _load_pending(db: Session, proposal_id: uuid.UUID) -> ApprovalRequestModel:
    proposal = (
        db.query(ApprovalRequestModel)
        .filter(
            ApprovalRequestModel.id == proposal_id,
            ApprovalRequestModel.target_type == service.PROPOSAL_TARGET_TYPE,
        )
        .one_or_none()
    )
    if proposal is None:
        raise AppError(404, "proposal_not_found", "No such policy proposal.")
    if proposal.status != "pending":
        raise AppError(
            409, "proposal_already_decided",
            f"This proposal was already {proposal.status}.",
        )
    return proposal
