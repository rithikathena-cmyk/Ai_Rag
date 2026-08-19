"""Per-category configuration validation for GuardrailPolicyModel.configuration.

Every create/update write path MUST call validate_configuration(category,
raw_dict) BEFORE the value ever reaches the ORM — this is the "Policy
Validation" stage in the spec's own Frontend -> API -> RBAC -> Policy
Validation -> Database -> Policy Engine -> Guardrail Runtime diagram. This
module is the single place a category's configuration shape is defined, so
a policy row can never be persisted with a shape nothing else recognizes,
and never via best-effort/partial validation.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.errors import AppError
from app.core.roles import ROLE_VALUES
from app.models.guardrail_policy import GUARDRAIL_POLICY_ACTIONS, GUARDRAIL_POLICY_CATEGORIES
from app.services.guardrail_policy.regex_safety import test_pattern_safety

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
MatchMode = Literal["EXACT", "WORD", "PHRASE", "REGEX"]
PIIDetectionSource = Literal["regex", "presidio", "gliner"]

# PII entities whose protection must never be disabled by a single
# unconfirmed click (spec §8/§9) — checked by the service layer (not this
# module) to route a disabling change through the approval workflow instead
# of applying it immediately. Kept here since it's a property of the PII
# category's own domain, alongside the config shape for that category.
#: Entities whose protection cannot be weakened without an approval step.
#:
#: Originally credentials only. Widened to include the high-consequence
#: personal identifiers: permitting an SSN, card number, Aadhaar, PAN,
#: passport or bank account is a disclosure decision of the same magnitude as
#: permitting an API key, and the Policy Copilot's own specification lists
#: "ALLOW SSN", "ALLOW CREDIT_CARD" and "ALLOW AADHAAR" as changes that must
#: never apply automatically.
#:
#: Membership here does not forbid a change — it routes it through
#: `service.py::update_policy`'s approval workflow instead of applying it
#: immediately.
#: Protection strength, weakest to strongest. The single source of truth for
#: "is this change a weakening" — service.py's approval gating and the Policy
#: Copilot's risk model both read it, so the two cannot disagree about whether
#: a given change reduces protection.
#:
#: ESCALATE sits just under BLOCK: it stops the request like BLOCK does, but
#: routes to a human rather than refusing outright.
ACTION_STRENGTH: dict[str, int] = {
    "ALLOW": 0, "FLAG": 1, "MASK": 2, "REDACT": 3, "ESCALATE": 4, "BLOCK": 5,
}


def is_weaker(proposed: str, than: str) -> bool:
    """True when `proposed` provides less protection than `than`. Unknown
    actions are treated as maximally strong so an unrecognised value can never
    be mistaken for a weakening and slip past an approval gate."""
    return ACTION_STRENGTH.get(proposed, 99) < ACTION_STRENGTH.get(than, 99)


CRITICAL_PII_ENTITIES = frozenset({
    # credentials
    "PASSWORD", "API_KEY", "ACCESS_TOKEN", "SECRET", "JWT",
    # high-consequence personal identifiers
    "SSN", "CREDIT_CARD", "AADHAAR", "PAN", "PASSPORT", "BANK_ACCOUNT",
})


class _StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class PIIPolicyConfig(_StrictModel):
    entity: str = Field(min_length=1, max_length=64)
    # Independent per-direction actions (spec: "SSN: Input=REDACT,
    # Output=BLOCK" must be configurable separately) — this is PII's real
    # source of truth; GuardrailPolicyModel's own top-level `action` column
    # is derived from these two (see service.py's _effective_action()) for
    # display/back-compat only, never independently editable for this
    # category. Validated against the same GUARDRAIL_POLICY_ACTIONS
    # membership validate_action() checks — inlined here rather than calling
    # that function to avoid a forward-reference (it's defined below).
    input_action: str
    output_action: str
    severity: Severity = "MEDIUM"
    detection_sources: list[PIIDetectionSource] = Field(default_factory=lambda: ["regex", "presidio", "gliner"])
    redaction_format: str | None = Field(default=None, max_length=64)
    # How many trailing characters a MASK leaves visible ("show the last four
    # digits"). Bounded at 8: this is the one knob whose whole purpose is to
    # let real characters through, so it gets an explicit ceiling rather than
    # trusting whatever a caller sends. None = the entity's built-in mask
    # shape. pii.py::_mask_last additionally refuses to reveal a whole value.
    reveal_last: int | None = Field(default=None, ge=1, le=8)
    # Roles that resolve to different actions from everyone else:
    #   {"hr": {"output_action": "ALLOW"}}
    # An OVERRIDE, not a replacement — a role with no entry gets the base
    # actions above, so adding a role to the system cannot leave it
    # unprotected. See pii_policy._apply_role_override.
    role_overrides: dict[str, dict[str, str | int]] = Field(default_factory=dict)
    # Only meaningful for an entity with no BUILT-IN recognizer (see
    # guardrail_policy/detector_capability.py's CONFIGURABLE_ENTITIES) — a
    # regex/shape pattern pii.py's _build_recognizers() loads at runtime to
    # give that entity real detection for the first time. Ignored (must be
    # None) for every entity that already has a built-in recognizer, so a
    # typo'd pattern can never silently shadow a real, tested one — enforced
    # in validate_configuration() below, not here (needs entities.py, which
    # this module deliberately doesn't import at class-definition time to
    # avoid a field validator depending on import order). Same ReDoS gate as
    # REGEX-category rules.
    detector_pattern: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("detector_pattern")
    @classmethod
    def _safe_detector_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return v
        test_pattern_safety(v)
        return v

    @field_validator("entity")
    @classmethod
    def _normalize_entity(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("input_action", "output_action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in GUARDRAIL_POLICY_ACTIONS:
            raise ValueError(f"action must be one of {GUARDRAIL_POLICY_ACTIONS}")
        return v

    @field_validator("detection_sources")
    @classmethod
    def _non_empty_sources(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("detection_sources must not be empty — disable the policy instead of clearing sources")
        return v

    @field_validator("role_overrides")
    @classmethod
    def _valid_overrides(cls, v: dict) -> dict:
        """Every key checked, because this is the one field that can hand a
        specific role more than everybody else gets. A typo'd role name would
        otherwise sit in the row looking like an active exception while
        matching nobody — or, worse, be read as one after a rename."""
        cleaned: dict[str, dict[str, str | int]] = {}
        for role, entry in v.items():
            key = str(role).strip().lower()
            if key not in ROLE_VALUES:
                raise ValueError(f"unknown role {role!r} in role_overrides; expected one of {ROLE_VALUES}")
            if not isinstance(entry, dict) or not entry:
                raise ValueError(f"role_overrides[{key}] must be a non-empty object")
            slots: dict[str, str | int] = {}
            for slot, value in entry.items():
                if slot in ("input_action", "output_action"):
                    if value not in GUARDRAIL_POLICY_ACTIONS:
                        raise ValueError(f"role_overrides[{key}].{slot} must be one of {GUARDRAIL_POLICY_ACTIONS}")
                    slots[slot] = value
                elif slot == "reveal_last":
                    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 8:
                        raise ValueError(f"role_overrides[{key}].reveal_last must be an integer between 1 and 8")
                    slots[slot] = value
                else:
                    raise ValueError(
                        f"role_overrides[{key}] may only set input_action, output_action or reveal_last"
                    )
            cleaned[key] = slots
        return cleaned


class RegexRuleConfig(_StrictModel):
    pattern: str = Field(min_length=1)
    entity: str = Field(min_length=1, max_length=64)

    @field_validator("pattern")
    @classmethod
    def _safe_pattern(cls, v: str) -> str:
        test_pattern_safety(v)  # raises AppError on unsafe/invalid pattern
        return v

    @field_validator("entity")
    @classmethod
    def _normalize_entity(cls, v: str) -> str:
        return v.strip().upper()


class WordRuleConfig(_StrictModel):
    word: str = Field(min_length=1, max_length=256)
    match_mode: MatchMode = "WORD"
    case_sensitive: bool = False
    # REGEX-mode word rules go through the same ReDoS gate as a real regex
    # rule ("word" is just the pattern in that mode) — enforced in
    # validate_configuration() below, once match_mode is known, rather than
    # a per-field validator here.


class SemanticThresholdConfig(_StrictModel):
    threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class PromptInjectionConfig(_StrictModel):
    threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    max_attempts: int | None = Field(default=None, ge=1, le=100)


class MessageLimitConfig(_StrictModel):
    # Bounds mirror this app's own existing defaults (Settings.
    # guardrail_max_input_chars=4000) with headroom in both directions —
    # spec §10: "Admin cannot set 0, negative values, or unreasonably large
    # values."
    max_input_chars: int = Field(ge=100, le=100_000)
    max_output_chars: int = Field(ge=100, le=100_000)


_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "PII": PIIPolicyConfig,
    "REGEX": RegexRuleConfig,
    "WORD_FILTER": WordRuleConfig,
    "SEMANTIC": SemanticThresholdConfig,
    "PROMPT_INJECTION": PromptInjectionConfig,
    "MESSAGE_LIMIT": MessageLimitConfig,
}


def _enforce_detector_capability(config: dict) -> dict:
    """The create/update-time half of "never allow an active policy for an
    entity with no enforceable detector" (guardrail_policy/
    detector_capability.py's module docstring) — the DB-free half, since
    every fact this needs (does a built-in recognizer exist, is this entity
    configurable, was a pattern supplied or does a default exist) is static.
    Runs for every PII write, not just ones the Policy Copilot proposes —
    closing the gap where a policy created directly through the Policy
    Center's own form (or the raw REST API) previously validated and
    persisted cleanly for an entity like BANK_ACCOUNT with nothing behind
    it at all (see entities.py's own docstring on why that is the most
    dangerous possible outcome).

    Imported here, not at module scope: entities.py/detector_capability.py
    sit in the same package and this keeps that dependency local to the one
    function that needs it, mirroring how guardrail_policy/service.py
    already imports pii_policy lazily to avoid a cycle.
    """
    from app.services.guardrail_policy.detector_capability import CONFIGURABLE_ENTITIES, DEFAULT_DETECTOR_PATTERNS
    from app.services.guardrail_policy.entities import lookup

    entity = config["entity"]
    spec = lookup(entity)
    if spec is not None and spec.is_enforceable:
        if config.get("detector_pattern"):
            raise AppError(
                422, "detector_pattern_not_applicable",
                f"{entity} already has a built-in detector ({spec.detector}) — detector_pattern is only for "
                "entities with none.",
            )
        return config

    # No built-in recognizer for this entity.
    if entity not in CONFIGURABLE_ENTITIES:
        note = f" {spec.note}" if spec is not None and spec.note else ""
        raise AppError(
            422, "entity_not_enforceable",
            f"No detector exists for {entity}, and it cannot be configured — a policy for it would have no "
            f"effect at runtime and is refused rather than silently created.{note}",
        )

    pattern = config.get("detector_pattern") or DEFAULT_DETECTOR_PATTERNS.get(entity)
    if not pattern:
        raise AppError(
            422, "detector_pattern_required",
            f"No detector exists for {entity} yet. Supply a detector_pattern (a regex matching its shape) to "
            "create one — there is no standard default for this entity.",
        )
    # Filled in when the caller relied on the default (IFSC) rather than
    # supplying one explicitly, so the persisted row always states exactly
    # what pattern is actually in force — never an implicit default that
    # could silently change if DEFAULT_DETECTOR_PATTERNS is edited later.
    # Re-validated even when it came from DEFAULT_DETECTOR_PATTERNS rather
    # than trusting a hard-coded pattern is automatically safe — cheap, and
    # one less thing to have to keep in sync by hand.
    test_pattern_safety(pattern)
    config["detector_pattern"] = pattern
    return config


def validate_configuration(category: str, raw: dict) -> dict:
    if category not in GUARDRAIL_POLICY_CATEGORIES:
        raise AppError(422, "invalid_category", f"category must be one of {GUARDRAIL_POLICY_CATEGORIES}")
    model_cls = _CONFIG_MODELS[category]
    try:
        parsed = model_cls.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, or AppError raised from a field_validator
        if isinstance(exc, AppError):
            raise
        raise AppError(422, "invalid_configuration", f"Invalid configuration for category {category}: {exc}")

    if category == "WORD_FILTER" and parsed.match_mode == "REGEX":
        test_pattern_safety(parsed.word)

    config = parsed.model_dump()
    if category == "PII":
        config = _enforce_detector_capability(config)
    return config


def validate_action(action: str) -> str:
    if action not in GUARDRAIL_POLICY_ACTIONS:
        raise AppError(422, "invalid_action", f"action must be one of {GUARDRAIL_POLICY_ACTIONS}")
    return action
