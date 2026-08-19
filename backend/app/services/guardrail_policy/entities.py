"""The authoritative catalogue of PII entities and how each is actually
detected.

Why this exists: a policy can be written for any entity string, but the
runtime only ever emits labels its detectors produce. Without this registry
an administrator can create `BANK_ACCOUNT -> BLOCK`, see it validated,
approved and versioned, and reasonably conclude bank accounts are now
blocked — when in fact no detector emits that label, so the policy is inert.
A security control that silently does nothing is worse than an absent one,
because it stops anyone looking further.

The Policy Copilot consults this to warn on, or refuse, proposals targeting
an undetectable entity.

Every entry is derived from the real detectors:
  - `guardrails/pii.py::_build_recognizers()` — deterministic regex+validator
  - `guardrails/secrets.py::check_secrets()` — credential shapes
  - `guardrails/gliner_check.py::_DEFAULT_LABELS` — contextual NER
  - `guardrails/presidio_check.py::_ALLOWED_ENTITIES` — allowlisted types
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Detection(StrEnum):
    #: Regex + validator/checksum. Authoritative, phrasing-independent.
    DETERMINISTIC = "DETERMINISTIC"
    #: Shape-based credential scanning.
    SHAPE = "SHAPE"
    #: Model-based NER. Real coverage, but phrasing-sensitive.
    CONTEXTUAL = "CONTEXTUAL"
    #: No detector emits this label. A policy for it cannot take effect.
    NONE = "NONE"


@dataclass(frozen=True)
class EntitySpec:
    name: str
    detection: Detection
    detector: str
    note: str = ""

    @property
    def is_enforceable(self) -> bool:
        return self.detection is not Detection.NONE

    @property
    def is_reliable(self) -> bool:
        """Detected independently of how the user phrased the message."""
        return self.detection in (Detection.DETERMINISTIC, Detection.SHAPE)


_SPECS: tuple[EntitySpec, ...] = (
    # ---- deterministic: regex + validator ----------------------------------
    EntitySpec("SSN", Detection.DETERMINISTIC, "pii.py SSN_RE"),
    EntitySpec("CREDIT_CARD", Detection.DETERMINISTIC, "pii.py CREDIT_CARD_RE + Luhn"),
    EntitySpec("AADHAAR", Detection.DETERMINISTIC, "pii.py AADHAAR_RE + Verhoeff"),
    EntitySpec("PAN", Detection.DETERMINISTIC, "pii.py PAN_RE + format check"),
    EntitySpec("PHONE", Detection.DETERMINISTIC, "pii.py PHONE_CANDIDATE_RE + confidence gate"),
    EntitySpec("EMAIL", Detection.DETERMINISTIC, "pii.py EMAIL_RE"),
    EntitySpec("DATE_OF_BIRTH", Detection.DETERMINISTIC, "pii.py DOB_RE + context gate"),
    EntitySpec("IP_ADDRESS", Detection.DETERMINISTIC, "pii.py IPV4_RE"),

    # ---- shape-based credential scanning -----------------------------------
    EntitySpec("API_KEY", Detection.SHAPE, "secrets.py"),
    EntitySpec("PASSWORD", Detection.SHAPE, "secrets.py"),
    EntitySpec("ACCESS_TOKEN", Detection.SHAPE, "secrets.py"),
    EntitySpec("SECRET", Detection.SHAPE, "secrets.py"),
    EntitySpec(
        "JWT", Detection.SHAPE, "pii.py JWT_RE",
        note="Now has a dedicated recognizer (pii.py's JWT_RE, the same pattern secrets.py's "
             "check_secrets() uses) — previously caught only incidentally by that generic shape "
             "rule, with no JWT-specific PII policy possible.",
    ),

    # ---- contextual: NER only ----------------------------------------------
    EntitySpec(
        "ADDRESS", Detection.CONTEXTUAL, "gliner_check.py 'home address or mailing address'",
        note="Detected, but phrasing-sensitive: GLiNER scores below its 0.6 threshold "
             "for many valid phrasings. Not a substitute for a deterministic rule.",
    ),
    EntitySpec(
        "PASSPORT", Detection.CONTEXTUAL, "gliner_check.py 'government-issued identification number'",
        note="Shares a label with SSN; measured 0.727 when spelled out, 0.466 when abbreviated.",
    ),

    # ---- NOT DETECTED BY DEFAULT, BUT CONFIGURABLE ---------------------------
    # No BUILT-IN recognizer exists for these — this catalogue stays accurate
    # about the out-of-the-box state — but each CAN gain a real, admin-created
    # one without a code change: guardrail_policy/detector_capability.py's
    # CONFIGURABLE_ENTITIES lists exactly these three, and pii.py's
    # _build_recognizers() loads any active PII policy row's
    # configuration.detector_pattern for them at runtime. Use
    # detector_capability.capability_for() for the live, DB-aware state
    # (UNSUPPORTED/DISABLED/PENDING_APPROVAL/ENABLED) — this static table only
    # ever answers "does a detector ship in code for this entity."
    EntitySpec(
        "BANK_ACCOUNT", Detection.NONE, "-",
        note="No built-in recognizer is active by default. presidio's US_BANK_NUMBER is "
             "allowlisted but never matched in 68 evaluation scenarios; pii.py also has an "
             "opt-in BANK_ACCOUNT_RE gated behind settings.guardrail_bank_account_detection_enabled "
             "(off by default — genuinely ambiguous without a labeled context, see that pattern's "
             "own comment). Independently of that flag, a configurable detector can now be created "
             "via the Policy Copilot (e.g. \"create a BANK_ACCOUNT detector matching pattern <regex> "
             "and mask it\") — there is no universal standard shape, so an explicit pattern is "
             "required; no default is offered.",
    ),
    EntitySpec(
        "IFSC", Detection.NONE, "-",
        note="No built-in recognizer. Configurable via the Policy Copilot with a known default "
             "pattern (the RBI's own published IFSC format — 4 letters, a literal '0', 6 "
             "alphanumeric characters) offered automatically if no explicit pattern is supplied — "
             "see detector_capability.DEFAULT_DETECTOR_PATTERNS.",
    ),
    EntitySpec(
        "EMPLOYEE_ID", Detection.NONE, "-",
        note="Deliberately NOT detected, and deliberately NOT configurable: gliner_validators.py "
             "actively vetoes this deployment's own employee-ID format to prevent it being misread "
             "as a government ID (see PII-FP-01). Excluded from CONFIGURABLE_ENTITIES on purpose — "
             "this is a product decision that employee IDs are not PII, not an unimplemented gap.",
    ),
    EntitySpec(
        "CUSTOMER_ID", Detection.NONE, "-",
        note="No built-in recognizer — org-specific identifiers are unmodelled. Configurable via "
             "the Policy Copilot; no universal shape exists, so an explicit pattern is required.",
    ),
    EntitySpec(
        "VEHICLE_PLATE", Detection.NONE, "-",
        note="No built-in recognizer. Vehicle registration plates vary by jurisdiction and format. "
             "Configurable via the Policy Copilot; no universal pattern is offered — an explicit "
             "format pattern is required (e.g. `\\d{3}-[A-Z]{3}` for a US-style format).",
    ),
)

ENTITY_REGISTRY: dict[str, EntitySpec] = {s.name: s for s in _SPECS}

#: Every entity a policy may legitimately name.
KNOWN_ENTITIES = frozenset(ENTITY_REGISTRY)

#: Entities where a policy will actually do something at runtime.
ENFORCEABLE_ENTITIES = frozenset(n for n, s in ENTITY_REGISTRY.items() if s.is_enforceable)

#: Entities named in policy but with no detector — a policy here is inert.
UNENFORCEABLE_ENTITIES = KNOWN_ENTITIES - ENFORCEABLE_ENTITIES


def lookup(entity: str) -> EntitySpec | None:
    return ENTITY_REGISTRY.get(entity.strip().upper())


def enforceability_warning(entity: str) -> str | None:
    """Human-readable caveat for a proposal targeting this entity, or None if
    the entity is deterministically detected and needs no caveat."""
    spec = lookup(entity)
    if spec is None:
        return f"{entity!r} is not a recognised PII entity."
    if spec.detection is Detection.NONE:
        return (
            f"No detector emits {spec.name}, so this policy would have no runtime effect. "
            f"{spec.note}"
        )
    if spec.detection is Detection.CONTEXTUAL:
        return (
            f"{spec.name} is detected only by contextual NER ({spec.detector}), which is "
            f"phrasing-sensitive — enforcement will be inconsistent. {spec.note}"
        )
    return None
