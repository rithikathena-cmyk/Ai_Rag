"""Guardrail Policy Center domain logic — the one place GuardrailPolicyModel
rows are actually created/mutated (mirrors services/employee_pii/service.py's
and services/projects/service.py's shape: routers/guardrail_policies.py and
routers/approvals.py call into this, nothing else does).

Every mutation:
  1. validates its configuration (services/guardrail_policy/validation.py)
  2. enforces optimistic locking (expected_version must match the row's
     current version, or the whole app's only compare-and-swap precedent —
     see this module's own docstring on why nothing else in this codebase
     could be reused here)
  3. writes a GuardrailPolicyVersionModel row in the SAME transaction —
     history is never overwritten
  4. invalidates the in-process policy cache (store.py) so the runtime
     checks pick the change up immediately, not after a restart
  5. emits a PII-safe audit event (services/audit/logger.py)

Disabling a CRITICAL_PII_ENTITIES policy is the one mutation this module
refuses to apply directly — it queues an ApprovalRequestModel instead,
reusing routers/approvals.py's existing generic approval infrastructure
wholesale (see apply_decision() below, dispatched from
routers/approvals.py::decide_approval() exactly like employee_pii_service's
own apply_decision()).
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.request_context import get_current_request_id
from app.models.approval_request import ApprovalRequestModel
from app.models.guardrail_policy import GuardrailPolicyModel, GuardrailPolicyVersionModel
from app.models.user import UserModel
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.guardrail_policy import store
from app.services.guardrail_policy.validation import (
    CRITICAL_PII_ENTITIES, is_weaker, validate_action, validate_configuration,
)

# Keys an update request may set — deliberately an explicit allowlist (same
# convention as employee_pii/service.py's _WRITABLE_FIELDS), never "any key
# the request body sends" applied via setattr in a loop.
_UPDATABLE_FIELDS = ("name", "description", "enabled", "action", "priority", "configuration", "mode")

# BLOCK > ESCALATE > REDACT > MASK > FLAG > ALLOW — used to derive
# GuardrailPolicyModel's single top-level `action` column for PII rows,
# which carry independent input_action/output_action instead (see
# validation.py::PIIPolicyConfig). The top-level column stays for
# display/back-compat with every other category's single-action model but
# is never independently caller-settable for PII — always recomputed here.
_ACTION_PRECEDENCE = {"BLOCK": 5, "ESCALATE": 4, "REDACT": 3, "MASK": 2, "FLAG": 1, "ALLOW": 0}
_THRESHOLD_APPROVAL_FLOOR = 0.5

logger = logging.getLogger(__name__)



def _effective_pii_action(configuration: dict) -> str:
    input_action = configuration.get("input_action", "FLAG")
    output_action = configuration.get("output_action", "REDACT")
    return max((input_action, output_action), key=lambda a: _ACTION_PRECEDENCE.get(a, 0))


def _overrides_weaken(config: dict, baseline: dict[str, str]) -> bool:
    """True when any role_overrides entry gives some role less protection than
    `baseline`.

    Without this, a per-role exception would be the one way to relax a
    critical entity without passing an approval gate: both gates below compare
    only the top-level input_action/output_action, and "HR sees the whole SSN"
    changes neither of them. Granting one role full visibility is a
    disclosure decision of the same size as granting everybody it — it just
    affects fewer people.
    """
    overrides = (config or {}).get("role_overrides") or {}
    if not isinstance(overrides, dict):
        return False
    return any(
        is_weaker(entry.get(key), current)
        for entry in overrides.values() if isinstance(entry, dict)
        for key, current in baseline.items()
        if entry.get(key) is not None
    )


def _is_critical_pii_weakening(policy: GuardrailPolicyModel, updates: dict) -> bool:
    """Broadened from the original "disable only" check: a critical entity
    (PASSWORD/API_KEY/ACCESS_TOKEN/SECRET) moving either direction's action
    AWAY from BLOCK is exactly as much a protection weakening as disabling
    the policy outright — both must go through approval, not apply
    immediately."""
    if policy.category != "PII":
        return False
    entity = (policy.configuration or {}).get("entity", "")
    if entity not in CRITICAL_PII_ENTITIES:
        return False
    if updates.get("enabled") is False:
        return True
    new_config = updates.get("configuration")
    if new_config is None:
        return False
    old_config = policy.configuration or {}
    if any(
        old_config.get(key) == "BLOCK" and new_config.get(key) not in (None, "BLOCK")
        for key in ("input_action", "output_action")
    ):
        return True
    # Measured against the actions the row will HAVE after this update, so an
    # override is judged against what everyone else ends up with rather than
    # against a value the same update is replacing.
    effective = {
        key: new_config.get(key, old_config.get(key))
        for key in ("input_action", "output_action")
        if new_config.get(key, old_config.get(key)) is not None
    }
    return _overrides_weaken(new_config, effective)


def _is_critical_pii_creation_weakening(category: str, configuration: dict) -> bool:
    """SF-09. The UPDATE gate above is a diff — it compares an existing row's
    action against the proposed one — so it cannot fire when there is no prior
    row. That is not an edge case: most entities have NO row at all and run on
    _SAFE_PII_DEFAULTS, which makes CREATE the ordinary way to permit a
    critical entity, and the one path with no approval gate.

    The absent row is not "no policy" — it is the safe default. So a create is
    a weakening whenever the row being created is weaker than the default it
    replaces, measured with the same ACTION_STRENGTH ordering the update gate
    and the Copilot's risk model use.
    """
    if category != "PII":
        return False
    entity = (configuration or {}).get("entity", "")
    if not entity or entity.upper() not in CRITICAL_PII_ENTITIES:
        return False

    # Imported here rather than at module scope: pii_policy imports from this
    # package's store, and a top-level import would close that cycle.
    from app.services.guardrail_policy.pii_policy import resolve_pii_policy

    baseline = resolve_pii_policy(entity)
    against = {"input_action": baseline.input_action, "output_action": baseline.output_action}
    if any(
        is_weaker(configuration.get(key), current)
        for key, current in against.items()
        if configuration.get(key) is not None
    ):
        return True
    return _overrides_weaken(
        configuration,
        {key: configuration.get(key, current) for key, current in against.items()},
    )


def _is_significant_threshold_weakening(policy: GuardrailPolicyModel, updates: dict) -> bool:
    """A concrete, documented rule rather than a vague "significant" call:
    SEMANTIC/PROMPT_INJECTION thresholds crossing below 0.5 (e.g. the
    spec's own 0.80 -> 0.20 example) require approval; anything staying at
    or above 0.5, or moving within the sub-0.5 range, does not."""
    if policy.category not in ("SEMANTIC", "PROMPT_INJECTION"):
        return False
    new_config = updates.get("configuration")
    if new_config is None:
        return False
    old_threshold = (policy.configuration or {}).get("threshold")
    new_threshold = new_config.get("threshold")
    if old_threshold is None or new_threshold is None:
        return False
    return old_threshold >= _THRESHOLD_APPROVAL_FLOOR > new_threshold


def _audit(event_type: AuditEventType, *, outcome: AuditOutcome, actor: UserModel, policy: GuardrailPolicyModel, reason_code: str | None = None) -> None:
    audit_logger.log(
        event_type, outcome=outcome, request_id=get_current_request_id(),
        actor_id=actor.id, actor_role=actor.role, resource_type="GUARDRAIL_POLICY",
        resource_id=str(policy.id), action=event_type.value, reason_code=reason_code,
        metadata={"guardrail_category": policy.category, "detail": policy.policy_key},
    )


class PolicyUpdateResult:
    """Tagged result of update_policy() — either the change applied
    immediately (`policy` set) or it was a critical-PII disable and got
    queued instead (`approval` set). Exactly one is non-None."""

    def __init__(self, policy: GuardrailPolicyModel | None = None, approval: ApprovalRequestModel | None = None):
        self.policy = policy
        self.approval = approval


def create_policy(
    db: Session, *, policy_key: str, name: str, description: str | None, category: str, action: str,
    priority: int, configuration: dict, mode: str, created_by: UserModel, pre_approved: bool = False,
) -> PolicyUpdateResult:
    """Returns a PolicyUpdateResult, not a policy (SF-09).

    Creating a row can weaken protection just as editing one can — when the
    row being created is weaker than the safe default it replaces. Since that
    is now gated, this has the same two possible outcomes as update_policy:
    the policy was created (`policy` set), or the change was queued for
    approval instead (`approval` set).

    pre_approved=True skips the gate, and is passed only by the Policy
    Copilot's approve route after a POLICY_APPROVE holder has already approved
    the change with full impact and simulation in front of them.
    """
    if db.query(GuardrailPolicyModel).filter(GuardrailPolicyModel.policy_key == policy_key).one_or_none() is not None:
        raise AppError(409, "policy_key_already_exists", f"A policy with key {policy_key!r} already exists")

    validated_config = validate_configuration(category, configuration)
    # PII rows carry independent input_action/output_action instead of a
    # single caller-chosen action — whatever the caller passed for `action`
    # is discarded in favor of the derived value (see _effective_pii_action).
    validated_action = _effective_pii_action(validated_config) if category == "PII" else validate_action(action)

    if _is_critical_pii_creation_weakening(category, validated_config):
        if pre_approved:
            # Still recorded — only the redundant second approval is skipped.
            logger.warning(
                "policy_create_weakens_critical_entity_pre_approved entity=%s actor=%s",
                validated_config.get("entity"), created_by.id,
            )
        else:
            approval = ApprovalRequestModel(
                action="create", target_type="guardrail_policy",
                # No row exists yet, so this identifies the pending creation
                # itself; the payload carries everything needed to apply it.
                target_id=uuid.uuid4(),
                requested_by=created_by.id, role=created_by.role,
                payload={
                    "create": {
                        "policy_key": policy_key, "name": name, "description": description,
                        "category": category, "action": validated_action, "priority": priority,
                        "configuration": validated_config, "mode": mode,
                    },
                    "approval_reason_code": "critical_pii_weakened_on_create",
                },
                status="pending",
            )
            db.add(approval)
            db.commit()
            db.refresh(approval)
            return PolicyUpdateResult(approval=approval)

    policy = GuardrailPolicyModel(
        policy_key=policy_key, name=name, description=description, category=category, enabled=True,
        action=validated_action, priority=priority, configuration=validated_config, mode=mode, version=1,
        created_by=created_by.id, updated_by=created_by.id,
    )
    db.add(policy)
    db.flush()
    db.add(
        GuardrailPolicyVersionModel(
            policy_id=policy.id, version=1, changed_by=created_by.id,
            previous_configuration=None, new_configuration=validated_config, reason="initial creation",
        )
    )
    db.commit()
    db.refresh(policy)
    store.invalidate()
    _audit(AuditEventType.POLICY_CREATED, outcome=AuditOutcome.SUCCESS, actor=created_by, policy=policy)
    return PolicyUpdateResult(policy=policy)


def update_policy(
    db: Session, policy_id: uuid.UUID, *, expected_version: int, updates: dict, updated_by: UserModel,
    reason: str | None = None, pre_approved: bool = False,
) -> PolicyUpdateResult:
    """pre_approved=True skips the automatic approval-request branch below.

    Its ONLY caller is the Policy Copilot's approve endpoint, and only after a
    holder of POLICY_APPROVE has explicitly approved a proposal that already
    displayed the full impact analysis, blast radius and simulated output —
    strictly more review than this branch's own approval row provides.
    Escalating again there would ask the same person to approve the same
    change twice.

    Defaults to False so every existing caller is completely unaffected: the
    Policy Center's own PATCH route still escalates exactly as before. The
    weakening is still detected and still audited when pre_approved is set —
    only the second approval row is skipped, never the record of it.
    """
    policy = db.get(GuardrailPolicyModel, policy_id)
    if policy is None:
        raise AppError(404, "policy_not_found", f"Guardrail policy {policy_id} not found")
    if policy.version != expected_version:
        raise AppError(
            409, "stale_policy_version",
            f"This policy was changed by someone else (current version {policy.version}, expected {expected_version}) "
            "— reload and try again",
        )

    unknown = set(updates) - set(_UPDATABLE_FIELDS)
    if unknown:
        raise AppError(422, "invalid_update_field", f"Unknown update field(s): {sorted(unknown)}")

    if "configuration" in updates:
        updates["configuration"] = validate_configuration(policy.category, updates["configuration"])
    if "action" in updates:
        updates["action"] = validate_action(updates["action"])
    if policy.category == "PII":
        # Never independently caller-settable for PII — always recomputed
        # from (possibly just-updated) configuration.input_action/
        # output_action, discarding whatever "action" the caller sent.
        merged_config = updates.get("configuration", policy.configuration or {})
        updates["action"] = _effective_pii_action(merged_config)

    approval_reason_code: str | None = None
    if _is_critical_pii_weakening(policy, updates):
        approval_reason_code = "critical_pii_weakened"
    elif _is_significant_threshold_weakening(policy, updates):
        approval_reason_code = "threshold_weakened"

    if approval_reason_code is not None and pre_approved:
        # Detected and recorded, but not re-escalated — see the docstring.
        _audit(
            AuditEventType.POLICY_APPROVED, outcome=AuditOutcome.SUCCESS, actor=updated_by,
            policy=policy, reason_code=f"pre_approved:{approval_reason_code}",
        )
        approval_reason_code = None

    if approval_reason_code is not None:
        approval = ApprovalRequestModel(
            action="update", target_type="guardrail_policy", target_id=policy.id,
            requested_by=updated_by.id, role=updated_by.role,
            payload={
                "updates": updates, "expected_version": expected_version, "reason": reason,
                "approval_reason_code": approval_reason_code,
            },
            status="pending",
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        _audit(
            AuditEventType.POLICY_DISABLED, outcome=AuditOutcome.DENIED, actor=updated_by, policy=policy,
            reason_code="approval_required",
        )
        return PolicyUpdateResult(approval=approval)

    _apply_update(db, policy, updates, changed_by=updated_by, reason=reason)
    event_type = _event_for_update(updates)
    _audit(event_type, outcome=AuditOutcome.SUCCESS, actor=updated_by, policy=policy)
    return PolicyUpdateResult(policy=policy)


def _event_for_update(updates: dict) -> AuditEventType:
    if updates.get("enabled") is True:
        return AuditEventType.POLICY_ENABLED
    if updates.get("enabled") is False:
        return AuditEventType.POLICY_DISABLED
    return AuditEventType.POLICY_UPDATED


def _apply_update(
    db: Session, policy: GuardrailPolicyModel, updates: dict, *, changed_by: UserModel, reason: str | None,
) -> None:
    previous_configuration = dict(policy.configuration or {})
    for field, value in updates.items():
        setattr(policy, field, value)
    policy.version += 1
    policy.updated_by = changed_by.id
    db.add(
        GuardrailPolicyVersionModel(
            policy_id=policy.id, version=policy.version, changed_by=changed_by.id,
            previous_configuration=previous_configuration, new_configuration=dict(policy.configuration or {}),
            reason=reason,
        )
    )
    db.commit()
    db.refresh(policy)
    store.invalidate()


def rollback_policy(
    db: Session, policy_id: uuid.UUID, *, expected_version: int, target_version: int, changed_by: UserModel,
) -> GuardrailPolicyModel:
    policy = db.get(GuardrailPolicyModel, policy_id)
    if policy is None:
        raise AppError(404, "policy_not_found", f"Guardrail policy {policy_id} not found")
    if policy.version != expected_version:
        raise AppError(409, "stale_policy_version", f"Current version is {policy.version}, expected {expected_version}")

    target_row = (
        db.query(GuardrailPolicyVersionModel)
        .filter(GuardrailPolicyVersionModel.policy_id == policy_id, GuardrailPolicyVersionModel.version == target_version)
        .one_or_none()
    )
    if target_row is None:
        raise AppError(404, "policy_version_not_found", f"Version {target_version} not found for this policy")

    _apply_update(
        db, policy, {"configuration": target_row.new_configuration}, changed_by=changed_by,
        reason=f"rollback to v{target_version}",
    )
    _audit(AuditEventType.POLICY_ROLLBACK, outcome=AuditOutcome.SUCCESS, actor=changed_by, policy=policy)
    return policy


def apply_decision(
    db: Session, approval: ApprovalRequestModel, decision: str, decider: UserModel, values: dict | None = None,
) -> ApprovalRequestModel:
    """Dispatched from routers/approvals.py::decide_approval() for
    target_type="guardrail_policy" — same call shape as
    employee_pii_service.apply_decision(). Does NOT flip
    approval.status/decided_by/decided_at/reason; the caller owns that."""
    policy = db.get(GuardrailPolicyModel, approval.target_id)
    if policy is None:
        raise AppError(404, "policy_not_found", f"Guardrail policy {approval.target_id} not found")

    payload = approval.payload or {}

    if decision == "rejected":
        _audit(AuditEventType.POLICY_REJECTED, outcome=AuditOutcome.DENIED, actor=decider, policy=policy)
        return approval

    expected_version = payload.get("expected_version")
    if expected_version is not None and policy.version != expected_version:
        raise AppError(
            409, "stale_policy_version",
            f"Policy changed since this request was submitted (current version {policy.version}, "
            f"requested against {expected_version}) — reject and re-submit",
        )

    _apply_update(db, policy, payload.get("updates") or {}, changed_by=decider, reason=payload.get("reason"))
    _audit(AuditEventType.POLICY_APPROVED, outcome=AuditOutcome.SUCCESS, actor=decider, policy=policy)
    return approval
