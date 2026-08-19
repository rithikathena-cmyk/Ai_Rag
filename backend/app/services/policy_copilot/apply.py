"""Applying an approved Policy Copilot proposal.

Delegates entirely to `guardrail_policy/service.py` — the same create/update
functions the Policy Center uses. There is deliberately no separate write path:
a second way to change policy would be a second place for a bug to live, and
the Copilot would drift from the Policy Center over time.

A proposal may target an entity that has no policy row at all (most do — most
entities run on the built-in safe default). Those become a CREATE; entities
with an existing row become an UPDATE.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.guardrail_policy import GuardrailPolicyModel
from app.models.user import UserModel
from app.services.guardrail_policy import service as policy_service
from app.services.guardrail_policy import store


@dataclass
class AppliedChange:
    entity: str
    location: str
    action: str
    policy_key: str
    operation: str          # created | updated
    version: int


def _policy_key(entity: str) -> str:
    return f"pii.{entity.lower()}"


def _find_row(db: Session, entity: str) -> GuardrailPolicyModel | None:
    """Matched on the configuration's entity rather than the key, because a
    row created by hand in the Policy Center may use any key it likes — the
    entity is what the runtime resolver actually keys on."""
    rows = (
        db.query(GuardrailPolicyModel)
        .filter(GuardrailPolicyModel.category == "PII")
        .all()
    )
    target = entity.strip().upper()
    for row in rows:
        if ((row.configuration or {}).get("entity", "") or "").strip().upper() == target:
            return row
    return None


def _slot(location: str) -> str:
    return "input_action" if location == "INPUT" else "output_action"


def _role_overrides(exceptions: list[dict] | None) -> dict[str, dict[str, str | int]]:
    """`role_exceptions` (a flat list, one per role/location) reshaped into the
    nested `role_overrides` map `pii_policy._apply_role_override` reads. A role
    exempted in both directions produces ONE entry with two slots, matching how
    the base actions are stored.

    `reveal_last` is a single value per role (not per slot), matching how the
    base configuration already stores one top-level `reveal_last` shared by
    both directions — `_apply_role_override` reads `entry.get("reveal_last")`
    the same way regardless of which slot's MASK it applies to. Only carried
    when the exception's own action is MASK; an exception's reveal count for
    a different action would be a stale number sitting in the row, resurrected
    the next time some other proposal switches that role back to MASK.
    """
    overrides: dict[str, dict[str, str | int]] = {}
    for exception in exceptions or []:
        role = str(exception["role"]).strip().lower()
        entry = overrides.setdefault(role, {})
        entry[_slot(exception["location"])] = exception["action"]
        reveal = exception.get("reveal_last")
        if reveal and exception["action"] == "MASK":
            entry["reveal_last"] = int(reveal)
    return overrides


def _rule_policy_key(prefix: str, label: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in label.strip().lower()).strip("_") or "rule"
    return f"{prefix}.{slug}"


def apply_word_rule(
    db: Session, *, word_rule: dict, approver: UserModel, reason: str | None,
) -> AppliedChange:
    """Applies an approved CREATE_WORD_RULE proposal. Delegates entirely to
    guardrail_policy/service.py::create_policy() — same function the Policy
    Center's own "New policy" form uses for a WORD_FILTER row — so a word
    rule proposed through the Copilot is indistinguishable at rest from one
    created by hand."""
    word = str(word_rule["word"]).strip()
    action = word_rule.get("action", "BLOCK")
    result = policy_service.create_policy(
        db,
        policy_key=_rule_policy_key("word", word),
        name=f"Blocked word: {word}",
        description=f"Created from a Policy Copilot proposal approved by {approver.role}.",
        category="WORD_FILTER",
        action=action,
        priority=100,
        configuration={
            "word": word,
            "match_mode": word_rule.get("match_mode", "WORD"),
            "case_sensitive": bool(word_rule.get("case_sensitive", False)),
        },
        mode="ENFORCE",
        created_by=approver,
        # The approver already reviewed this proposal before approving — see
        # update_policy's docstring for the same reasoning on the PII path.
        pre_approved=True,
    )
    if result.policy is None:
        raise AppError(
            409, "policy_creation_requires_approval",
            "Creating this word rule was queued for approval instead of applied.",
        )
    store.invalidate()
    return AppliedChange(
        entity=word, location="input", action=action,
        policy_key=result.policy.policy_key, operation="created", version=result.policy.version,
    )


def apply_regex_rule(
    db: Session, *, regex_rule: dict, approver: UserModel, reason: str | None,
) -> AppliedChange:
    """Applies an approved CREATE_REGEX_RULE proposal. Same delegation
    pattern as apply_word_rule() above — create_policy() re-runs the same
    ReDoS/validity gate validation.py already checked at proposal time."""
    pattern = str(regex_rule["pattern"])
    label = str(regex_rule["label"]).strip().upper()
    action = regex_rule.get("action", "BLOCK")
    result = policy_service.create_policy(
        db,
        policy_key=_rule_policy_key("regex", label),
        name=f"{label} pattern rule",
        description=f"Created from a Policy Copilot proposal approved by {approver.role}.",
        category="REGEX",
        action=action,
        priority=100,
        configuration={"pattern": pattern, "entity": label},
        mode="ENFORCE",
        created_by=approver,
        pre_approved=True,
    )
    if result.policy is None:
        raise AppError(
            409, "policy_creation_requires_approval",
            "Creating this regex rule was queued for approval instead of applied.",
        )
    store.invalidate()
    return AppliedChange(
        entity=label, location="input", action=action,
        policy_key=result.policy.policy_key, operation="created", version=result.policy.version,
    )


def apply_rollback(
    db: Session, *, entity: str, target_version: int, approver: UserModel, reason: str | None,
) -> AppliedChange:
    """"Rollback the PHONE policy to version 3" — the one Copilot mutation
    that isn't a create/update, so it doesn't go through apply_proposal()'s
    changes loop at all. Delegates entirely to guardrail_policy/service.py's
    existing rollback_policy() — the SAME function the Policy Center's own
    manual rollback button calls (routers/guardrail_policies.py) — which
    already reads the real version history (GuardrailPolicyVersionModel) and
    writes a POLICY_ROLLBACK audit event. Nothing here re-implements
    versioning or history; this is purely entity-name -> policy_id
    resolution plus the same pre_approved reasoning apply_proposal()'s other
    branches already use."""
    row = _find_row(db, entity)
    if row is None:
        raise AppError(404, "policy_not_found", f"No policy row exists for {entity} to roll back")

    updated = policy_service.rollback_policy(
        db, row.id, expected_version=row.version, target_version=target_version, changed_by=approver,
    )
    store.invalidate()
    return AppliedChange(
        entity=entity, location="input,output", action=(updated.configuration or {}).get("input_action", ""),
        policy_key=updated.policy_key, operation="rolled_back", version=updated.version,
    )


def apply_proposal(
    db: Session, *, changes: list[dict], approver: UserModel, reason: str | None,
    role_exceptions: list[dict] | None = None,
) -> list[AppliedChange]:
    """Apply every change in an approved proposal.

    Changes for the same entity in both directions are merged into ONE row —
    a PII policy row carries `input_action` and `output_action` together, so
    "block SSN in input and redact it in output" is a single row, not two.

    `role_exceptions` defaults to None rather than being required, so a
    proposal stored before per-role policy existed still applies cleanly — it
    simply carries no overrides.
    """
    by_entity: dict[str, dict[str, str]] = {}
    reveal_by_entity: dict[str, int] = {}
    detector_pattern_by_entity: dict[str, str] = {}
    for change in changes:
        entity = str(change["entity"]).strip().upper()
        by_entity.setdefault(entity, {})[_slot(change["location"])] = change["action"]
        # Only a MASK has trailing characters to reveal; carrying the count
        # onto any other action would leave a stale number in the row that a
        # later switch back to MASK would silently resurrect.
        if change.get("reveal_last") and change["action"] == "MASK":
            reveal_by_entity[entity] = int(change["reveal_last"])
        if change.get("detector_pattern"):
            detector_pattern_by_entity[entity] = str(change["detector_pattern"])

    overrides = _role_overrides(role_exceptions)

    applied: list[AppliedChange] = []
    for entity, actions in by_entity.items():
        row = _find_row(db, entity)

        if row is None:
            # No row yet: the entity is running on the safe default. Any
            # direction the proposal did not mention keeps that default,
            # rather than being silently set to something else.
            from app.services.guardrail_policy.pii_policy import resolve_pii_policy

            current = resolve_pii_policy(entity)
            config = {
                "entity": entity,
                "input_action": actions.get("input_action", current.input_action),
                "output_action": actions.get("output_action", current.output_action),
                "severity": "MEDIUM",
                "detection_sources": ["regex", "presidio", "gliner"],
            }
            if entity in reveal_by_entity:
                config["reveal_last"] = reveal_by_entity[entity]
            if overrides:
                config["role_overrides"] = overrides
            if entity in detector_pattern_by_entity:
                # An explicit pattern the proposal stated — create_policy()'s
                # own validate_configuration() re-validates it (ReDoS gate)
                # and, for a CONFIGURABLE entity with none supplied here,
                # falls back to DEFAULT_DETECTOR_PATTERNS itself (IFSC only)
                # — this branch only needs to pass through what was actually
                # proposed, never guess a default on its own.
                config["detector_pattern"] = detector_pattern_by_entity[entity]
            create_result = policy_service.create_policy(
                db,
                policy_key=_policy_key(entity),
                name=f"{entity} PII policy",
                description=f"Created from a Policy Copilot proposal approved by {approver.role}.",
                category="PII",
                action=config["input_action"],
                priority=100,
                configuration=config,
                mode="ENFORCE",
                created_by=approver,
                # The approver already reviewed impact and simulation before
                # approving — see update_policy's docstring for the same
                # reasoning applied to the edit path.
                pre_approved=True,
            )
            created = create_result.policy
            if created is None:
                # Only reachable if pre_approved were dropped; kept so a future
                # change to that flag surfaces loudly instead of silently
                # returning a proposal that looks applied.
                raise AppError(
                    409, "policy_creation_requires_approval",
                    f"Creating the {entity} policy was queued for approval instead of applied.",
                )
            applied.append(AppliedChange(
                entity=entity, location=",".join(sorted(actions)), action=config["input_action"],
                policy_key=created.policy_key, operation="created", version=created.version,
            ))
            continue

        if row.enabled:
            merged = dict(row.configuration or {})
        else:
            # SF-01 again, on the write side. A disabled row is not in force —
            # the safe default is — so the proposal was analysed, simulated and
            # approved against the DEFAULT, not against whatever this row still
            # contains. Re-enabling it with its stale configuration would apply
            # settings the approver never saw. Only the entity and the
            # proposal's own changes survive.
            from app.services.guardrail_policy.pii_policy import resolve_pii_policy

            current = resolve_pii_policy(entity)
            merged = {
                "input_action": current.input_action,
                "output_action": current.output_action,
                "severity": (row.configuration or {}).get("severity", "MEDIUM"),
                "detection_sources": ["regex", "presidio", "gliner"],
            }
        merged.update(actions)
        merged.setdefault("entity", entity)
        if entity in reveal_by_entity:
            merged["reveal_last"] = reveal_by_entity[entity]
        if overrides:
            # Merged per role, not replaced wholesale: an approved proposal
            # exempting HR must not silently revoke an exception granted to
            # some other role by an earlier, separately-approved proposal.
            existing = dict(merged.get("role_overrides") or {})
            for role, slots in overrides.items():
                existing[role] = {**(existing.get(role) or {}), **slots}
            merged["role_overrides"] = existing
        result = policy_service.update_policy(
            db, row.id,
            expected_version=row.version,
            updates={"configuration": merged, "enabled": True},
            updated_by=approver,
            reason=reason or "Approved via Policy Copilot",
            # The approver already reviewed impact, blast radius and simulated
            # output before approving — see update_policy's docstring.
            pre_approved=True,
        )
        policy = result.policy if result.policy is not None else row
        applied.append(AppliedChange(
            entity=entity, location=",".join(sorted(actions)),
            action=merged.get("input_action", ""), policy_key=policy.policy_key,
            operation="updated", version=policy.version,
        ))

    # The runtime resolver reads a cached snapshot; without this the change
    # would not take effect until the cache happened to expire.
    store.invalidate()
    return applied
