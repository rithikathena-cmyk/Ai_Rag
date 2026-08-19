"""The single place any PII detector resolves "what should happen for this
entity type, in this direction" — services/guardrails/pii.py's redact_pii()
is the only caller (see that module's docstring). No other guardrail check
loads PII policy independently, per the approved plan's "Policy Engine is
the single source of truth" requirement.

Resolution order for a given entity:
  1. An ENABLED GuardrailPolicyModel row exists (services/guardrail_policy/
     store.py's cache) → that row is authoritative, including an explicit
     ALLOW. Turning protection off is a real, supported operation — it just
     has to be said out loud, as `input_action: ALLOW` on an enabled row,
     where it is visible in the UI, risk-classified, approval-gated and
     recorded in the version history.
  2. A DISABLED row exists → _SAFE_PII_DEFAULTS (see SF-01 below).
  3. No row exists (or the policy store is unreachable — store.py's own
     fail-safe already falls back to its last-known-good cache, or an
     empty list on first-ever failure) → _SAFE_PII_DEFAULTS, matching the
     spec's own recommended defaults. Falls back to _GENERIC_DEFAULT
     for any entity with no explicit safe default either. This is what
     makes "policy unavailable" fail toward protection, never toward
     "allow everything".

SF-01 — why (2) changed
-----------------------
This module previously treated a disabled row as authoritative, on the
reasoning that "an admin turning protection off on purpose must actually
turn it off". Live evaluation showed why that is the wrong default:

    A leftover disabled test row keyed to CREDIT_CARD caused
    `_resolve_match()` to return status "allow", so real card numbers passed
    through the pipeline completely unredacted in BOTH directions, while an
    SSN in the same message was redacted normally.

The flaw is in what "disabled" communicates. In the Policy Center a disabled
row renders as an ordinary off toggle, which reads as "this custom rule is
not active" — not as "this entity now has no protection whatsoever". The two
readings differ by an entire security control, and the dangerous one was the
default.

Disabling a rule and permitting an entity are now distinct operations:

    disabled row              -> safe default          (protection retained)
    enabled row, ALLOW action -> ALLOW                 (protection removed, deliberately)

The second remains fully supported. It is simply no longer something you can
arrive at by accident, from a toggle that appears to be doing something else.
"""

from dataclasses import dataclass, replace

from app.services.guardrail_policy import store


@dataclass(frozen=True)
class PIIPolicyResolution:
    input_action: str
    output_action: str
    enabled: bool
    detection_sources: tuple[str, ...] = ("regex", "presidio", "gliner")
    redaction_format: str | None = None
    dry_run: bool = False
    #: Where these actions came from. "custom" = an enabled policy row;
    #: "default" = _SAFE_PII_DEFAULTS, either because no row exists or
    #: because the row that does exist is disabled. Surfaced so the UI can
    #: distinguish "protected by your rule" from "protected by the built-in
    #: default" — two states that look identical without it.
    source: str = "default"
    #: How many trailing characters MASK should leave visible. None means the
    #: entity's built-in mask shape (see pii.py::_mask_token). Only meaningful
    #: for a MASK action — REDACT/BLOCK replace the value entirely.
    reveal_last: int | None = None
    #: Which role's override produced this resolution, if any. Purely
    #: informational, so the UI and trace can show that a caller saw a
    #: role-specific policy rather than the base one.
    role_override_applied: str | None = None
    #: True when a row exists but is switched off. `source` is then
    #: "default": the entity is still protected, just not by that row. Lets
    #: the Policy Center show "disabled — safe default in effect" rather
    #: than implying the entity is unguarded.
    disabled_row_present: bool = False
    #: The resolved row's `version` when an enabled custom row governed this
    #: resolution; None for the built-in safe default (no row, or a
    #: disabled one). Purely informational — tags a captured PII occurrence
    #: (pii.py's PIIOccurrenceRecord) with which policy version was in force
    #: when it was redacted, for later audit; never used to make a decision.
    policy_version: int | None = None


# Two tiers, and only two, so an entity's treatment is predictable without
# consulting a per-type table:
#
#   personal data -> MASK in / REDACT out   (hidden, but the request works)
#   credentials   -> BLOCK in / BLOCK out   (refused outright)
#
# This replaces a three-way split that treated near-identical types
# inconsistently: SSN redacted while CREDIT_CARD blocked, and PHONE/EMAIL
# merely FLAGGED (detected, logged, and then left in the text verbatim).
# A user could not predict what would happen to their message, and the
# FLAG tier in particular meant two of the most commonly-pasted identifiers
# got the weakest treatment of all.
#
# Credentials stay BLOCK because they are not personal data with a
# legitimate reason to appear in a question — an API key or password in a
# chat message is always a mistake, and masking one still leaves it in the
# request that reaches the model. Refusing is the only useful response.
_PERSONAL_DATA = ("MASK", "REDACT")
_CREDENTIAL = ("BLOCK", "BLOCK")

_SAFE_PII_DEFAULTS: dict[str, tuple[str, str]] = {
    # entity -> (input_action, output_action)
    "SSN": _PERSONAL_DATA,
    "PAN": _PERSONAL_DATA,
    "AADHAAR": _PERSONAL_DATA,
    "PASSPORT": _PERSONAL_DATA,
    "BANK_ACCOUNT": _PERSONAL_DATA,
    "CREDIT_CARD": _PERSONAL_DATA,
    "PHONE": _PERSONAL_DATA,
    "EMAIL": _PERSONAL_DATA,
    "API_KEY": _CREDENTIAL,
    "PASSWORD": _CREDENTIAL,
    "ACCESS_TOKEN": _CREDENTIAL,
    "SECRET": _CREDENTIAL,
    # Previously absent, which meant a JWT fell through to _GENERIC_DEFAULT
    # (the PERSONAL_DATA tier) — wrong for a credential even before pii.py
    # had a real JWT recognizer of its own (see that module's JWT_RE), and
    # more consequential now that it does.
    "JWT": _CREDENTIAL,
}
# Any entity pii.py's recognizers can produce that isn't in the table above
# (e.g. IP_ADDRESS, DATE_OF_BIRTH, IBAN, EMPLOYEE_ID, MEDICAL_RECORD_NUMBER)
# is personal data by definition — same treatment as every other personal
# identifier, never a silent ALLOW and never the old weaker FLAG.
_GENERIC_DEFAULT = _PERSONAL_DATA


def _find_row(entity: str) -> object | None:
    for row in store.get_all_policies("PII"):
        if (row.configuration or {}).get("entity") == entity:
            return row
    return None


def _safe_default(entity: str, *, disabled_row_present: bool = False) -> PIIPolicyResolution:
    """The built-in protection for an entity. `enabled=True` because a safe
    default IS an active policy — it is the absence of a *custom* rule, not
    the absence of protection."""
    input_action, output_action = _SAFE_PII_DEFAULTS.get(entity, _GENERIC_DEFAULT)
    return PIIPolicyResolution(
        input_action=input_action, output_action=output_action, enabled=True,
        source="default", disabled_row_present=disabled_row_present,
    )


def _apply_role_override(resolution: PIIPolicyResolution, config: dict, role: str | None) -> PIIPolicyResolution:
    """Narrow a resolution to a specific role, if the row defines an override.

    Shape in `configuration`:

        "role_overrides": {"hr": {"output_action": "ALLOW"}}

    Deliberately an OVERRIDE rather than a replacement: a role with no entry
    gets the row's base actions, so adding a role to the system cannot
    silently leave it unprotected. The keys are role identifiers as
    `llm_rbac.yaml` spells them (`user`, `hr`, `project_manager`, `ceo`,
    `admin`).

    role=None means "no particular caller" — audit sanitisation, evaluation
    harnesses, the Copilot's own simulation — and always resolves to the base
    actions. That is the conservative choice: a caller that cannot state who
    it is does not get a role's relaxation.
    """
    overrides = (config or {}).get("role_overrides") or {}
    if not role or not isinstance(overrides, dict):
        return resolution
    entry = overrides.get(role.strip().lower())
    if not isinstance(entry, dict) or not entry:
        return resolution

    return replace(
        resolution,
        input_action=entry.get("input_action", resolution.input_action),
        output_action=entry.get("output_action", resolution.output_action),
        reveal_last=entry.get("reveal_last", resolution.reveal_last),
        role_override_applied=role.strip().lower(),
    )


def resolve_pii_policy(entity: str, role: str | None = None) -> PIIPolicyResolution:
    """`role` narrows the result to that caller's override, when the row
    defines one. Omitting it resolves the base policy — see
    _apply_role_override for why that is the conservative default."""
    entity = entity.strip().upper()
    row = _find_row(entity)

    if row is None:
        return _safe_default(entity)

    if not row.enabled:
        # SF-01. A disabled row is an inactive RULE, not a licence to stop
        # protecting the entity — see this module's header. Every field of
        # the row is ignored, not just its actions: its detection_sources
        # and DRY_RUN mode would otherwise keep applying from a rule the
        # operator believes they switched off.
        return _safe_default(entity, disabled_row_present=True)

    config = row.configuration or {}
    base = PIIPolicyResolution(
        input_action=config.get("input_action", _GENERIC_DEFAULT[0]),
        output_action=config.get("output_action", _GENERIC_DEFAULT[1]),
        enabled=True,
        detection_sources=tuple(config.get("detection_sources") or ("regex", "presidio", "gliner")),
        redaction_format=config.get("redaction_format"),
        dry_run=row.mode == "DRY_RUN",
        source="custom",
        reveal_last=config.get("reveal_last"),
        # getattr, not row.version directly: several tests construct a
        # lightweight fake row (only the fields that test actually needs)
        # rather than a real CachedPolicy/ORM instance — version is purely
        # informational (see PIIPolicyResolution.policy_version's docstring),
        # so a fake without it degrades to None rather than crashing every
        # caller that doesn't happen to define this one extra attribute.
        policy_version=getattr(row, "version", None),
    )
    return _apply_role_override(base, config, role)
