"""Impact analysis and simulation for a proposed policy change.

Both are read-only. Simulation runs SYNTHETIC values through the real
detector and the real resolver — it never mutates policy, and never uses a
real value.

The risk model is deterministic and stated explicitly rather than being an
LLM's opinion, so the same proposal always carries the same risk rating and
an approver can predict it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.guardrail_policy.entities import Detection, lookup
from app.services.guardrail_policy.pii_policy import resolve_pii_policy
from app.services.guardrail_policy.validation import CRITICAL_PII_ENTITIES
from app.services.guardrails.pii import preview_redaction, redact_pii
from app.services.policy_copilot.knowledge import KNOWN_ROLES, ROLE_LABELS
from app.services.policy_copilot.schemas import PolicyIntent

#: Ordered weakest -> strongest. Moving DOWN this list weakens protection.
_STRENGTH = {"ALLOW": 0, "FLAG": 1, "MASK": 2, "REDACT": 3, "ESCALATE": 4, "BLOCK": 5}

#: Synthetic values only — published test PANs, the reserved SSN shape, the
#: 555-01xx fictional phone range, example.com (RFC 2606). Never a real value.
_SYNTHETIC: dict[str, str] = {
    "SSN": "123-45-6789",
    "CREDIT_CARD": "4111 1111 1111 1111",
    "AADHAAR": "2345 6789 0123",
    "PAN": "ABCDE1234F",
    "PHONE": "555-0142",
    "EMAIL": "jane.doe@example.com",
    "DATE_OF_BIRTH": "1985-03-12",
    "IP_ADDRESS": "203.0.113.42",
    "ADDRESS": "42 Baker Street, London",
    "PASSPORT": "X1234567",
}

#: Every role whose requests pass through the PII pipeline, as
#: (identifier, label). The identifier is how `llm_rbac.yaml` and
#: `pii_policy.role_overrides` spell the role; the label is what an approver
#: reads. Derived from knowledge.py rather than restated, so a role can only
#: be added in one place.
_ROLES: tuple[tuple[str, str], ...] = tuple(
    (identifier, ROLE_LABELS.get(identifier, identifier)) for identifier in KNOWN_ROLES
)
_ALL_ROLES = tuple(label for _, label in _ROLES)


@dataclass
class RoleEffect:
    """What one role would see after the change. Present on every proposal,
    not only those with exceptions — an approver granting HR an exception
    needs to see, in the same table, that nobody else got one."""

    role: str            # identifier, as stored in role_overrides
    label: str           # human-readable
    action: str
    sample: str | None
    is_exception: bool = False


@dataclass
class ImpactReport:
    entity: str
    location: str
    current_action: str
    proposed_action: str
    direction: str                      # WEAKENS | STRENGTHENS | UNCHANGED
    risk: str                           # CRITICAL | HIGH | MEDIUM | LOW
    exposure: str                       # HIGH | MEDIUM | LOW | NONE
    affected_roles: tuple[str, ...]
    affected_flows: tuple[str, ...]
    blast_radius: str
    notes: list[str] = field(default_factory=list)
    current_sample: str | None = None
    proposed_sample: str | None = None
    #: Trailing characters a MASK leaves visible. None = the entity's built-in
    #: mask shape.
    reveal_last: int | None = None
    role_effects: list[RoleEffect] = field(default_factory=list)


def _direction(current: str, proposed: str) -> str:
    c, p = _STRENGTH.get(current, 3), _STRENGTH.get(proposed, 3)
    if p < c:
        return "WEAKENS"
    return "STRENGTHENS" if p > c else "UNCHANGED"


def _risk(entity: str, direction: str, proposed: str) -> str:
    if direction != "WEAKENS":
        return "LOW"
    if entity in CRITICAL_PII_ENTITIES:
        return "CRITICAL" if proposed == "ALLOW" else "HIGH"
    return "HIGH" if proposed == "ALLOW" else "MEDIUM"


_RISK_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def _worst(a: str, b: str) -> str:
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


def _exposure(direction: str, proposed: str) -> str:
    if direction != "WEAKENS":
        return "NONE"
    return {"ALLOW": "HIGH", "FLAG": "HIGH", "MASK": "MEDIUM", "REDACT": "LOW"}.get(proposed, "LOW")


_EXPOSURE_ORDER = ("NONE", "LOW", "MEDIUM", "HIGH")


def _worst_exposure(a: str, b: str) -> str:
    return a if _EXPOSURE_ORDER.index(a) >= _EXPOSURE_ORDER.index(b) else b


def _simulate(entity: str, location: str) -> tuple[str | None, str | None]:
    """Run the synthetic value through the REAL detector under the CURRENT
    policy. The proposed column is rendered from the proposed action rather
    than by mutating policy — simulation must never write."""
    sample = _SYNTHETIC.get(entity)
    if sample is None:
        return None, None
    direction = "input" if location == "INPUT" else "output"
    current_text, _ = redact_pii(f"value: {sample}", direction=direction)
    return sample, current_text.replace("value: ", "")


def _render_proposed(entity: str, action: str, reveal_last: int | None = None) -> str | None:
    """Rendered by the REAL token builder rather than an approximation here.
    An approver reading "###0142" is being told exactly how many digits
    survive, so that string has to come from the code that will produce it."""
    sample = _SYNTHETIC.get(entity)
    if sample is None:
        return None
    if action == "FLAG":
        return f"{sample}   (recorded, value left in place)"
    return preview_redaction(entity, sample, action=action, reveal_last=reveal_last)


def _role_effects(
    entity: str, location: str, base_action: str, reveal_last: int | None, intent: PolicyIntent
) -> list[RoleEffect]:
    """One row per role, so a per-role exception is legible as a difference
    from the others rather than as a claim on its own."""
    exceptions = {
        e.role: e
        for e in intent.role_exceptions
        if e.location == location
    }
    effects: list[RoleEffect] = []
    for identifier, label in _ROLES:
        exc = exceptions.get(identifier)
        is_exception = exc is not None
        action = exc.action if exc else base_action
        # An ALLOW/REDACT/BLOCK exception carries no reveal_last of its own —
        # it replaces the action outright. A MASK exception uses ITS OWN
        # reveal count ("employees see last 2, HR sees last 4"), never the
        # base change's — those are independent numbers for independent roles.
        effect_reveal = exc.reveal_last if (exc and exc.action == "MASK") else (None if is_exception else reveal_last)
        effects.append(RoleEffect(
            role=identifier,
            label=label,
            action=action,
            sample=_render_proposed(entity, action, effect_reveal),
            is_exception=is_exception,
        ))
    return effects


def analyze(intent: PolicyIntent) -> list[ImpactReport]:
    reports: list[ImpactReport] = []
    for change in intent.changes:
        entity = change.normalized_entity()
        resolution = resolve_pii_policy(entity)
        current = resolution.input_action if change.location == "INPUT" else resolution.output_action

        direction = _direction(current, change.action)
        spec = lookup(entity)

        notes: list[str] = []
        if resolution.disabled_row_present:
            # Mutually exclusive with the message below: both are "the safe
            # default is in force", but an admin who sees a disabled row in
            # the Policy Center needs to be told it is not the thing
            # protecting them, or the two views appear to disagree.
            notes.append(
                "A disabled policy row exists for this entity. It is NOT in force — protection is "
                "currently provided by the built-in safe default. Re-enabling that row would "
                "replace the default with whatever it configures."
            )
        elif resolution.source == "default":
            notes.append(
                "No custom policy exists yet for this entity; the current action is the built-in "
                "safe default."
            )
        if spec is not None and not spec.is_reliable:
            notes.append(
                f"Detection for {entity} is {spec.detection.lower()}, so enforcement of any action "
                "will be less consistent than for a deterministically-detected entity."
            )
        if direction == "WEAKENS":
            notes.append(
                "This reduces protection. Review the simulated output below before approving."
            )
        if change.reveal_last and change.action != "MASK":
            notes.append(
                f"A reveal of {change.reveal_last} trailing characters only applies to MASK; "
                f"{change.action} replaces the value entirely, so it will be ignored."
            )

        exceptions = [e for e in intent.role_exceptions if e.location == change.location]
        if exceptions and spec is not None and spec.detection is not Detection.DETERMINISTIC:
            # GLiNER and Presidio redact their spans BEFORE the policy-aware
            # pass runs, with no role and no policy — so an exception only
            # reaches values the deterministic recognizers claim. Saying so
            # here is the difference between an approver granting the
            # exception they think they are granting and one that silently
            # does nothing for half the matches.
            notes.append(
                f"{entity} is detected by {spec.detector} rather than a deterministic pattern. "
                "Role exceptions apply only to values the deterministic recognizers claim; anything "
                "the ML detectors catch first is redacted for every role, exempted or not."
            )
        for exc in exceptions:
            reveal_note = f" (last {exc.reveal_last} visible)" if exc.action == "MASK" and exc.reveal_last else ""
            notes.append(
                f"{ROLE_LABELS.get(exc.role, exc.role)} is exempted from this rule and will see "
                f"{exc.action}{reveal_note} instead of {change.action} on {change.location}."
            )

        if exceptions:
            blast = (
                "Every request from every role except "
                + ", ".join(sorted({ROLE_LABELS.get(e.role, e.role) for e in exceptions}))
                + ", which is exempted."
            )
        else:
            blast = "Every request from every role."

        # An exception is a weakening for the role that receives it, so the
        # report's risk is the worst of the base change and every exception.
        # Rating a proposal LOW because its *base* action is strict, while it
        # simultaneously grants a role full visibility, is exactly the reading
        # error an approver must not be led into.
        risk = _risk(entity, direction, change.action)
        exposure = _exposure(direction, change.action)
        for exc in exceptions:
            exc_direction = _direction(current, exc.action)
            risk = _worst(risk, _risk(entity, exc_direction, exc.action))
            exposure = _worst_exposure(exposure, _exposure(exc_direction, exc.action))

        current_sample, current_rendered = _simulate(entity, change.location)
        reports.append(ImpactReport(
            entity=entity,
            location=change.location,
            current_action=current,
            proposed_action=change.action,
            direction=direction,
            risk=risk,
            exposure=exposure,
            affected_roles=_ALL_ROLES,
            affected_flows=("CHAT", "RAG", "TRACE", "UI") if change.location == "OUTPUT" else ("CHAT", "TRACE", "UI"),
            blast_radius=blast,
            notes=notes,
            current_sample=current_rendered,
            proposed_sample=_render_proposed(entity, change.action, change.reveal_last),
            reveal_last=change.reveal_last,
            role_effects=_role_effects(entity, change.location, change.action, change.reveal_last, intent),
        ))
    return reports


def overall_risk(reports: list[ImpactReport]) -> str:
    worst = "LOW"
    for r in reports:
        worst = _worst(worst, r.risk)
    return worst
