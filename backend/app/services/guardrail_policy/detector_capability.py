"""Detector Capability Registry — the single place that answers "does an
entity have anything real backing a PII policy for it, right now" as one of
four states:

    UNSUPPORTED       no detector exists, and none can be configured either
    DISABLED          a detector exists (built-in or admin-configured), but
                       no active policy row is currently enforcing anything
                       custom for it — the built-in safe default governs
                       (see pii_policy.py's SF-01: this is a normal, common,
                       perfectly safe state, not a problem to flag)
    PENDING_APPROVAL  a create/update targeting this entity is queued in
                       ApprovalRequestModel, awaiting a POLICY_APPROVE holder
    ENABLED           a detector exists AND an enabled custom policy row
                       governs this entity

This module makes NO detection decisions and enforces NOTHING at runtime —
it is a read-only classification layer other modules consult:

  - guardrail_policy/validation.py's PIIPolicyConfig uses CONFIGURABLE_ENTITIES/
    DEFAULT_DETECTOR_PATTERNS (the static, DB-free half of this module) as
    the create/update-time gate: "never allow an active policy for an entity
    with no enforceable detector."
  - policy_copilot/validation.py calls capability_for() (the DB-aware half)
    to decide whether a natural-language proposal targeting an undetectable
    entity should be refused outright (EMPLOYEE_ID, BANK_ACCOUNT with no
    pattern offered) or turned into a detector-creation proposal instead
    (IFSC, which has a known default shape; CUSTOMER_ID/BANK_ACCOUNT, which
    need an admin-supplied pattern).
  - pii.py's _build_recognizers() reads the SAME store rows this module
    reads (via guardrail_policy/store.py, already the one live-policy cache
    every guardrail check shares) to build a runtime recognizer for a
    configured entity — this module does not build recognizers itself, it
    only classifies capability for reporting/gating purposes.

Every configurable entity here has an EXPLICIT product decision behind it
(see the "Entity handling" list this module's tests are named after) —
EMPLOYEE_ID is deliberately excluded (see entities.py's own note: employee
IDs are actively vetoed from being read as PII, not merely undetected), so
it is never added to CONFIGURABLE_ENTITIES no matter how this module
evolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.models.approval_request import ApprovalRequestModel
from app.services.guardrail_policy import store
from app.services.guardrail_policy.entities import Detection, lookup


class DetectorState(StrEnum):
    UNSUPPORTED = "UNSUPPORTED"
    DISABLED = "DISABLED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ENABLED = "ENABLED"


#: Entities with no built-in recognizer that MAY still gain one, via an
#: admin-supplied (or, for IFSC, a well-known default) regex/shape pattern
#: stored on a PII policy row's `configuration.detector_pattern` — see
#: pii.py's _build_recognizers() for where that pattern actually becomes a
#: live recognizer. Every other Detection.NONE entity (BANK_ACCOUNT's and
#: CUSTOMER_ID's siblings not listed here, and EMPLOYEE_ID specifically)
#: stays UNSUPPORTED with no path to configure one — narrow and explicit by
#: design, matching entities.py's own "add a new entry only for a concrete,
#: evidence-based reason" discipline.
CONFIGURABLE_ENTITIES = frozenset({"BANK_ACCOUNT", "IFSC", "CUSTOMER_ID", "VEHICLE_PLATE"})

#: A default pattern offered when the admin doesn't supply one — only for
#: entities with a real, standardized, publicly-documented shape. IFSC is
#: the Reserve Bank of India's own published format (4 letters identifying
#: the bank, a literal '0', then 6 alphanumeric characters identifying the
#: branch) — a safe, well-known default. BANK_ACCOUNT and CUSTOMER_ID have
#: no such universal standard (see pii_patterns.py's own BANK_ACCOUNT_RE
#: comment: "genuinely ambiguous... order id, reference code, phone number"),
#: so both are deliberately absent here — an admin must supply an explicit
#: pattern for those, never a guessed one.
DEFAULT_DETECTOR_PATTERNS: dict[str, str] = {
    "IFSC": r"\b[A-Z]{4}0[A-Z0-9]{6}\b",
}


@dataclass(frozen=True)
class DetectorCapability:
    entity: str
    state: DetectorState
    #: "built-in" (entities.py already lists a real code-level recognizer),
    #: "configured" (an admin-created detector_pattern row exists),
    #: "configurable" (no row yet, but CONFIGURABLE_ENTITIES allows one),
    #: or "none" (no detector, and none can be configured).
    detector_source: str
    #: The pattern currently backing detection, if any — the row's own
    #: detector_pattern when configured, else DEFAULT_DETECTOR_PATTERNS'
    #: entry when one exists and none has been configured yet (a preview of
    #: what would be used, not something already active).
    pattern: str | None
    explanation: str


def _configured_pattern(entity: str) -> tuple[str | None, bool]:
    """Returns (pattern, enabled) for the most relevant PII policy row
    naming this entity with a detector_pattern set, or (None, False) if
    none exists. `enabled` mirrors GuardrailPolicyModel.enabled — SF-01
    doesn't apply here the way it does to actions: a disabled DETECTOR row
    means the runtime genuinely has no recognizer for this entity (there is
    no "safe default" shape to fall back to for something that was never
    built-in), so DISABLED for a configurable entity means exactly that,
    unlike DISABLED for a built-in one."""
    for row in store.get_all_policies("PII"):
        config = row.configuration or {}
        if (config.get("entity") or "").strip().upper() != entity:
            continue
        pattern = config.get("detector_pattern")
        if pattern:
            return pattern, row.enabled
    return None, False


def _has_pending_creation(db: Session, entity: str) -> bool:
    """True if an ApprovalRequestModel row is queued (status='pending') to
    CREATE a PII policy for this entity — the CREATE path is where a
    detector-creation proposal actually lives (see guardrail_policy/
    service.py::create_policy()'s SF-09 approval-queueing branch); an
    UPDATE to an existing row is not a capability change (the detector, if
    any, already exists), so it is deliberately not checked here."""
    pending = (
        db.query(ApprovalRequestModel)
        .filter(
            ApprovalRequestModel.target_type == "guardrail_policy",
            ApprovalRequestModel.action == "create",
            ApprovalRequestModel.status == "pending",
        )
        .all()
    )
    for approval in pending:
        create = (approval.payload or {}).get("create") or {}
        if create.get("category") != "PII":
            continue
        if ((create.get("configuration") or {}).get("entity") or "").strip().upper() == entity:
            return True
    return False


def capability_for(entity: str, db: Session | None = None) -> DetectorCapability:
    """The full, DB-aware classification. `db` is optional — omitting it
    skips only the PENDING_APPROVAL check (falls through to whatever the
    live policy store already resolves to), for callers that don't have a
    session handy and don't need that distinction (e.g. pii.py's own hot
    path, which never calls this at all — see module docstring)."""
    entity = entity.strip().upper()
    spec = lookup(entity)

    if spec is not None and spec.is_enforceable:
        # A real, built-in recognizer already exists (DETERMINISTIC/SHAPE/
        # CONTEXTUAL) — capability here is just "is a custom row currently
        # enforcing something," never whether detection itself is possible.
        row_enabled = any(
            (row.configuration or {}).get("entity", "").strip().upper() == entity
            for row in store.get_active_policies("PII")
        )
        if row_enabled:
            return DetectorCapability(
                entity, DetectorState.ENABLED, "built-in", None,
                f"{entity} is detected by {spec.detector}, governed by an active custom policy.",
            )
        if db is not None and _has_pending_creation(db, entity):
            return DetectorCapability(
                entity, DetectorState.PENDING_APPROVAL, "built-in", None,
                f"{entity} is detected by {spec.detector}; a policy change is awaiting approval.",
            )
        return DetectorCapability(
            entity, DetectorState.DISABLED, "built-in", None,
            f"{entity} is detected by {spec.detector}; no custom policy is active, so the built-in safe default applies.",
        )

    # Detection.NONE (or an entity KNOWN_ENTITIES doesn't even list) from
    # here down — no built-in recognizer.
    configured_pattern, row_enabled = _configured_pattern(entity)
    if configured_pattern:
        state = DetectorState.ENABLED if row_enabled else DetectorState.DISABLED
        return DetectorCapability(
            entity, state, "configured", configured_pattern,
            f"{entity} is detected by an admin-configured pattern "
            f"({'active' if row_enabled else 'currently disabled — no detector runs for this entity'}).",
        )

    if db is not None and _has_pending_creation(db, entity):
        return DetectorCapability(
            entity, DetectorState.PENDING_APPROVAL,
            "configurable" if entity in CONFIGURABLE_ENTITIES else "none", None,
            f"A detector for {entity} has been proposed and is awaiting approval.",
        )

    if entity in CONFIGURABLE_ENTITIES:
        default = DEFAULT_DETECTOR_PATTERNS.get(entity)
        return DetectorCapability(
            entity, DetectorState.UNSUPPORTED, "configurable", default,
            (
                f"No detector exists for {entity} yet. "
                + (
                    f"A standard pattern is available ({default!r}) and can be enabled by creating a policy for it."
                    if default
                    else "It can be configured by supplying a regex/shape pattern, e.g. "
                    f"\"create a {entity} detector matching pattern <pattern> and mask it in input\"."
                )
            ),
        )

    return DetectorCapability(
        entity, DetectorState.UNSUPPORTED, "none", None,
        f"No detector exists for {entity}, and it cannot be configured — a policy targeting it would have no effect.",
    )
