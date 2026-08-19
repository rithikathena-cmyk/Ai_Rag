"""Deterministic validation of an interpreted intent.

Runs AFTER interpretation and BEFORE any proposal is created. Nothing here
consults a model — this is the layer that decides whether an interpretation
is permitted to become a proposal at all, so it must be reproducible and
inspectable.

The caller's authority is re-derived from their real role here. It is never
read from the intent, because the intent originated in text the caller
controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import AppError
from app.core.permissions import Permission
from app.services.guardrail_policy.detector_capability import CONFIGURABLE_ENTITIES, DEFAULT_DETECTOR_PATTERNS
from app.services.guardrail_policy.entities import enforceability_warning, lookup
from app.services.guardrail_policy.regex_safety import test_pattern_safety
from app.services.guardrail_policy.validation import CRITICAL_PII_ENTITIES, is_weaker
from app.services.llm_rbac import policy_loader
from app.services.policy_copilot.knowledge import KNOWN_ROLES, ROLE_LABELS
from app.services.policy_copilot.schemas import IntentType, PolicyIntent


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_approval: bool = False

    def fail(self, message: str) -> "ValidationResult":
        self.valid = False
        self.errors.append(message)
        return self


def _has(role: str, permission: Permission) -> bool:
    granted = policy_loader.role_config(role).granted_permissions
    return permission.value in granted or "*" in granted


def validate(intent: PolicyIntent, *, role: str) -> ValidationResult:
    """The ten checks, in order of how early they can rule a request out."""
    result = ValidationResult(valid=True)

    # 1. Refused intents never proceed, whatever the caller's authority.
    if intent.intent is IntentType.REFUSED:
        return result.fail(intent.message or "This request is not permitted.")

    if intent.intent is IntentType.CLARIFICATION_NEEDED:
        return result.fail(intent.message or "The request was ambiguous.")

    # 2. Authority for the KIND of operation, re-derived from the real role.
    needed = Permission.POLICY_PROPOSE if intent.is_mutating else Permission.POLICY_READ
    if not _has(role, needed):
        return result.fail(
            f"Role {role!r} does not hold {needed.value}, which this operation requires."
        )

    # 3/4. Entities must be real. An unknown entity is refused rather than
    # created, because a policy naming a label no detector emits is inert.
    unknown = intent.unknown_entities()
    if unknown:
        return result.fail(
            f"Unrecognised PII {'entity' if len(unknown) == 1 else 'entities'}: "
            f"{', '.join(unknown)}."
        )

    # 5. Enforceability. A policy for an undetectable entity would validate,
    # approve and version cleanly while doing nothing at runtime — the most
    # dangerous possible outcome, because it stops anyone looking further.
    # CONFIGURABLE_ENTITIES is the one exception: for those, "no detector
    # yet" is not necessarily a refusal — it can become a detector-creation
    # proposal instead, see below.
    changes_by_entity = {c.normalized_entity(): c for c in intent.changes}
    for entity in set(changes_by_entity) | ({intent.entity.strip().upper()} if intent.entity else set()):
        spec = lookup(entity)
        warning = enforceability_warning(entity)
        if warning is None:
            continue
        if spec is not None and not spec.is_enforceable and intent.is_mutating:
            if entity not in CONFIGURABLE_ENTITIES:
                return result.fail(warning)
            change = changes_by_entity.get(entity)
            pattern = (change.detector_pattern if change else None) or DEFAULT_DETECTOR_PATTERNS.get(entity)
            if not pattern:
                return result.fail(
                    f"No detector exists for {entity} yet. Supply a regex/shape pattern to create one, "
                    f"for example \"create a {entity} detector matching pattern <pattern> and "
                    f"{(change.action if change else 'MASK').lower()} it in "
                    f"{(change.location if change else 'input').lower()}\"."
                )
            try:
                test_pattern_safety(pattern)
            except AppError as exc:
                return result.fail(str(exc.detail))
            result.requires_approval = True
            result.warnings.append(
                f"No detector exists for {entity} yet — approving this proposal will CREATE one "
                f"(pattern: {pattern!r}) in addition to the policy change."
            )
            continue
        result.warnings.append(warning)

    if not intent.is_mutating:
        return result

    # 5b. Word/regex rule creation — separate from the PII `changes` shape
    # below, since neither carries an `entity`/`location` pair. Checked here,
    # before the PII-specific checks that follow, since none of those apply.
    if intent.intent is IntentType.CREATE_WORD_RULE:
        if intent.word_rule is None or not intent.word_rule.word.strip():
            return result.fail("Which word or phrase should be blocked?")
        return result
    if intent.intent is IntentType.CREATE_REGEX_RULE:
        if intent.regex_rule is None:
            return result.fail("No concrete policy change was identified in that request.")
        if not intent.regex_rule.pattern:
            return result.fail(
                f"What pattern should the {intent.regex_rule.label} rule match? For example: "
                f"\"add this regex for {intent.regex_rule.label}: `EMP-\\d{{6}}`\"."
            )
        # Same ReDoS/validity gate guardrail_policy.service.create_policy()
        # applies at write time — checked here too so an unsafe pattern is
        # refused with a specific reason at proposal time, not a generic
        # failure when the approver later tries to apply it.
        try:
            test_pattern_safety(intent.regex_rule.pattern)
        except AppError as exc:
            return result.fail(str(exc.detail))
        return result

    # 6. A mutating intent must actually say what to change. DISABLE and
    # ROLLBACK are exempt: they carry their target in `entity` and
    # `target_version` rather than in a change list.
    if not intent.changes and intent.intent not in (IntentType.DISABLE_POLICY, IntentType.ROLLBACK_POLICY):
        return result.fail("No concrete policy change was identified in that request.")

    # 7. Conflicts — the same entity/location given two different actions.
    seen: dict[tuple[str, str], str] = {}
    for change in intent.changes:
        key = (change.normalized_entity(), change.location)
        if key in seen and seen[key] != change.action:
            return result.fail(
                f"Conflicting actions for {key[0]} on {key[1]}: {seen[key]} and {change.action}."
            )
        seen[key] = change.action

    # 8. Weakening a critical entity requires approval. Not a refusal — a
    # gate. `service.py::update_policy` enforces the same rule on the write
    # path; this surfaces it in the proposal so the approver sees it coming.
    for change in intent.changes:
        if change.normalized_entity() in CRITICAL_PII_ENTITIES and change.action in ("ALLOW", "FLAG"):
            result.requires_approval = True
            result.warnings.append(
                f"{change.normalized_entity()} is a critical entity; setting it to "
                f"{change.action} weakens protection and requires explicit approval."
            )

    # 8b. Role exceptions. An exception is how "everyone sees the masked
    # number, HR sees all of it" is expressed, and it is a real relaxation for
    # the role that receives it — so it is gated the same way a global
    # weakening is, and never inferred loosely (see interpreter's
    # _find_role_exceptions).
    base_by_location = {c.location: c.action for c in intent.changes}
    for exception in intent.role_exceptions:
        if exception.role not in KNOWN_ROLES:
            return result.fail(f"Unknown role {exception.role!r} in a role exception.")

        base = base_by_location.get(exception.location)
        if base is None:
            return result.fail(
                f"A {exception.role} exception was given for {exception.location}, but the request "
                f"does not change any policy on {exception.location}."
            )

        entity = intent.entity.strip().upper() if intent.entity else ""
        label = ROLE_LABELS.get(exception.role, exception.role)
        if entity in CRITICAL_PII_ENTITIES:
            result.requires_approval = True
            result.warnings.append(
                f"{entity} is a critical entity; exempting {label} from its protection "
                "requires explicit approval."
            )
        elif is_weaker(exception.action, base):
            result.requires_approval = True
            result.warnings.append(
                f"{label} is exempted from {base} and will see {entity or 'this entity'} "
                f"as {exception.action}. Every other role keeps {base}."
            )

    # 9. Disabling is never a shortcut to ALLOW. Since SF-01 a disabled row
    # falls back to the safe default, so this is a caveat rather than a
    # refusal — but an admin expecting "disable == permit" must be corrected.
    if intent.intent is IntentType.DISABLE_POLICY:
        result.requires_approval = True
        result.warnings.append(
            "Disabling this policy does NOT permit the entity — protection reverts to the "
            "built-in safe default. To permit it deliberately, set the action to ALLOW instead."
        )

    # 10. Rollback needs a target.
    if intent.intent is IntentType.ROLLBACK_POLICY and intent.target_version is None:
        return result.fail("Specify which version to roll back to, for example \"rollback to version 18\".")

    return result
