"""Read-only view of the active PII policy for every known entity.

Answers "what is actually in force right now", which is the question behind
most of the Copilot's read intents. Reads the live resolver, so it reflects
DB rows, disabled rows and safe defaults exactly as the runtime sees them —
there is no second source of truth here to drift.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.guardrail_policy.detector_capability import CONFIGURABLE_ENTITIES, capability_for
from app.services.guardrail_policy.entities import ENTITY_REGISTRY, enforceability_warning
from app.services.guardrail_policy.pii_policy import resolve_pii_policy
from app.services.guardrail_policy.validation import CRITICAL_PII_ENTITIES
from app.services.policy_copilot.knowledge import KNOWN_ROLES


#: One source for the role list — a role missing here would silently stop
#: having its exception displayed while still having it enforced.
_ROLES = KNOWN_ROLES


def role_overrides(entity: str) -> dict[str, dict]:
    """Which roles resolve to something other than the base policy.

    Derived by asking the resolver once per role rather than by reading the
    row's `role_overrides` map, so this view cannot disagree with enforcement:
    whatever a role actually gets at runtime is what appears here.
    """
    base = resolve_pii_policy(entity)
    differing: dict[str, dict] = {}
    for role in _ROLES:
        resolved = resolve_pii_policy(entity, role)
        # Only the slots that actually differ. Reporting the role's full
        # resolved triple would list values it inherits from the base policy
        # under a heading that says "override", which reads as a wider
        # exception than the one that was granted.
        slots = {
            key: getattr(resolved, key)
            for key in ("input_action", "output_action", "reveal_last")
            if getattr(resolved, key) != getattr(base, key)
        }
        if slots:
            differing[role] = slots
    return differing


def list_active_policies(db: Session | None = None) -> list[dict]:
    """`db` is optional so existing callers (and every current test) keep
    working unchanged — omitting it only means capability_for() cannot
    report PENDING_APPROVAL (see that function's own docstring), everything
    else about the row is identical either way."""
    rows: list[dict] = []
    for name, spec in sorted(ENTITY_REGISTRY.items()):
        resolution = resolve_pii_policy(name)
        capability = capability_for(name, db)
        rows.append({
            "entity": name,
            "input_action": resolution.input_action,
            "output_action": resolution.output_action,
            # "custom" = an enabled policy row is in force.
            # "default" = the built-in safe default is in force.
            "source": resolution.source,
            # True when a row exists but is switched off. The entity is still
            # protected — by the default — and the UI must say so rather than
            # implying it is unguarded.
            "disabled_row_present": resolution.disabled_row_present,
            "dry_run": resolution.dry_run,
            # Trailing characters a MASK leaves visible, and any role that
            # resolves to something other than the base actions above.
            "reveal_last": resolution.reveal_last,
            "role_overrides": role_overrides(name),
            "detection": spec.detection.value,
            "detector": spec.detector,
            "enforceable": spec.is_enforceable,
            "reliable": spec.is_reliable,
            "critical": name in CRITICAL_PII_ENTITIES,
            "warning": enforceability_warning(name),
            # UNSUPPORTED | DISABLED | PENDING_APPROVAL | ENABLED — see
            # guardrail_policy/detector_capability.py's module docstring.
            "capability_state": capability.state.value,
            "capability_source": capability.detector_source,
            "capability_explanation": capability.explanation,
            "configurable": name in CONFIGURABLE_ENTITIES,
        })
    return rows
