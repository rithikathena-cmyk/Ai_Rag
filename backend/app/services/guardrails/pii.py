import hashlib
import hmac
import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Literal

from app.core.config import settings
from app.services.guardrail_policy.pii_policy import resolve_pii_policy
from app.services.guardrails import pii_patterns, pii_validators
from app.services.guardrails.types import GuardrailStep

logger = logging.getLogger(__name__)

NAME = "pii_redact"

_INSECURE_DEFAULT_SALT = "dev-insecure-pii-salt-change-me"


def _ensure_safe_hash_secret() -> None:
    """Fails closed instead of silently hashing with a hard-coded key: if
    hash mode is on outside development but the salt is still the
    checked-into-source default, a hash keyed on a value anyone can read in
    this repo isn't a privacy control at all — refuse rather than proceed
    with a false sense of protection."""
    if settings.guardrail_pii_mode != "hash":
        return
    if settings.environment != "development" and settings.guardrail_pii_hash_salt == _INSECURE_DEFAULT_SALT:
        raise RuntimeError(
            "guardrail_pii_mode='hash' requires GUARDRAIL_PII_HASH_SALT to be set to a real secret "
            "outside development — refusing to hash PII with the checked-in default salt."
        )


def _hash_token(label: str, normalized_value: str) -> str:
    # HMAC (not a bare hash) specifically so a low-entropy value like a
    # phone number or PAN isn't crackable via a rainbow table once salted
    # with a real secret — see guardrail_pii_hash_salt's own comment in
    # config.py. Keyed on the *normalized* value so e.g. "John.Doe@MAIL.COM"
    # and "john.doe@mail.com" hash identically.
    _ensure_safe_hash_secret()
    digest = hmac.new(settings.guardrail_pii_hash_salt.encode(), normalized_value.encode(), hashlib.sha256).hexdigest()
    return f"[REDACTED_{label}_{digest[:8]}]"


def _placeholder_token(label: str, normalized_value: str) -> str:
    return f"[REDACTED_{label}]"


def _mask_phone(digits: str) -> str:
    """First 2 + last 1 digit visible, everything between replaced with '#',
    at the real digit count — enough that a user can recognize "yes, that's
    the number I meant" without it being enough to reconstruct or brute-force
    the number itself. is_valid_phone() floors accepted values at 7 digits
    (see that function's docstring), so len(digits) - 3 is never negative in
    practice; the len<=3 branch is a defensive fallback only, not a path any
    current recognizer can reach."""
    if len(digits) <= 3:
        return "#" * len(digits)
    return digits[:2] + "#" * (len(digits) - 3) + digits[-1:]


def _mask_email(value: str) -> str:
    """First 2 characters of the local part visible (1 if the local part is
    only 2 chars, 0 if it's a single char — never enough alone to reveal the
    whole local part), the rest of the local part replaced with '#' at its
    real length, and a fixed generic ".com" ending in place of the real
    domain — the actual domain (which can itself identify an org/department)
    is never shown in mask mode."""
    local, sep, _domain = value.partition("@")
    if not sep:
        return _placeholder_token("EMAIL", value)
    reveal = min(2, max(len(local) - 1, 0))
    return local[:reveal] + "#" * (len(local) - reveal) + ".com"


def _mask_last(value: str, reveal: int) -> str:
    """Leave the last `reveal` characters visible, mask the rest at the real
    length. Used when a policy sets `reveal_last` — e.g. a phone shown as
    ###0142 so an employee can confirm which number is meant without being
    able to reconstruct it.

    Revealing more digits is strictly more exposure, which is why this is an
    explicit per-policy opt-in rather than a default: the built-in shapes stay
    as they are unless an administrator asks for something different.

    Entity-agnostic — correct for PHONE/SSN/PAN/... where the trailing
    characters of the raw value ARE the meaningful, distinguishing part. NOT
    used for EMAIL — see _mask_email_last() for why that entity needs its own
    version of this same idea.
    """
    if reveal <= 0:
        return "#" * len(value)
    if reveal >= len(value):
        # Never reveal the whole value through a mask — that would be ALLOW
        # wearing a mask's name, and would not appear as ALLOW anywhere in
        # the UI, the trace or the approval workflow.
        reveal = max(len(value) - 1, 0)
    return "#" * (len(value) - reveal) + value[-reveal:] if reveal else "#" * len(value)


def _mask_email_last(value: str, reveal: int) -> str:
    """reveal_last for EMAIL specifically. _mask_last() applied to a whole
    email address reveals the literal trailing characters of the STRING —
    for the overwhelmingly common case that's the domain suffix (".com",
    ".org", ...), which is useless as a "which address did you mean"
    signal (nearly every address in a domain shares it) and, for anything
    other than a .com address, is a real regression: it shows the caller's
    REAL domain (e.g. ".org") where _mask_email()'s own default deliberately
    substitutes a generic ".com" specifically because a domain "can itself
    identify an org/department" (see that function's docstring). Found live:
    "show last 4 characters" on jane.doe@mycompany.org rendered
    "##################.org" — the real TLD, not the last 4 characters of
    anything a viewer could use to recognize the address.

    Fix: reveal_last is measured against the LOCAL part only, and the domain
    stays the same generic ".com" _mask_email() already uses regardless of
    the real domain — this function's whole job is answering "what would
    reveal_last mean for an email" without weakening the guarantee the
    entity's default masking already makes."""
    local, sep, _domain = value.partition("@")
    if not sep:
        return _placeholder_token("EMAIL", value)
    if reveal <= 0:
        return "#" * len(local) + ".com"
    if reveal >= len(local):
        reveal = max(len(local) - 1, 0)
    return ("#" * (len(local) - reveal) + local[-reveal:] if reveal else "#" * len(local)) + ".com"


def _mask_token(label: str, normalized_value: str, reveal_last: int | None = None) -> str:
    """Partial mask for PHONE/EMAIL only — the two types a real, load-bearing
    reveal format was specified for. Every other label falls back to the
    ordinary opaque placeholder token; masking format for those wasn't
    specified and inventing one risks exposing more than intended."""
    if reveal_last is not None:
        if label == "EMAIL":
            return _mask_email_last(normalized_value, reveal_last)
        return _mask_last(normalized_value, reveal_last)
    if label == "PHONE":
        return _mask_phone(normalized_value)
    if label == "EMAIL":
        return _mask_email(normalized_value)
    return _placeholder_token(label, normalized_value)


_TOKEN_BUILDERS: dict[str, Callable[[str, str], str]] = {
    "placeholder": _placeholder_token,
    "hash": _hash_token,
    "mask": _mask_token,
}


@dataclass(frozen=True)
class _Recognizer:
    label: str
    pattern: re.Pattern
    normalize: Callable[[str], str]  # raw match -> the key used for validation AND hashing
    is_valid: Callable[[str], bool] | None = None  # None means "regex structure alone is authoritative"
    group: int = 0  # which regex group is the PII span to redact; 0 = the whole match
    # Optional extra gate that needs the FULL text + match object, not just
    # the matched substring — used by PHONE (confidence depends on nearby
    # context words the matched span itself doesn't contain) and
    # DATE_OF_BIRTH (redaction requires nearby DOB context as a hard gate,
    # not a confidence tier — see pii_validators.has_dob_context()). Applied
    # in addition to `is_valid`, not instead of it.
    context_gate: Callable[[str, re.Match], bool] | None = None


def _build_recognizers() -> list[_Recognizer]:
    # Order matters — every subsequent recognizer only ever sees text the
    # earlier ones didn't already claim and redact:
    #  1. AADHAAR before PHONE: PHONE_CANDIDATE_RE's shape would otherwise
    #     happily match a bare 12-digit Aadhaar too.
    #  2. PHONE before CREDIT_CARD: an Indian number with country code and
    #     trunk prefix ("091-9876543210") totals 13 digits — exactly
    #     CREDIT_CARD's minimum — so PHONE must get first refusal;
    #     is_valid_phone()'s own digit cap keeps it from then swallowing
    #     genuine 13-16 digit card numbers it doesn't recognize as a phone
    #     shape (see that function's docstring).
    # EMAIL/SSN/PAN don't overlap with the digit-only types (need '@' or
    # letters) so their position relative to the others doesn't matter.
    recognizers = [
        _Recognizer("EMAIL", pii_patterns.EMAIL_RE, pii_validators.normalize_email),
        # Same label as the ordinary EMAIL recognizer above, deliberately —
        # from the reader's perspective a [REDACTED_EMAIL] token means "an
        # email address was here," regardless of which candidate pattern
        # caught it. Unlike EMAIL_RE, this DOES need an is_valid gate: the
        # regex's word/connector shape is deliberately loose (see that
        # pattern's own comment in pii_patterns.py), so
        # is_valid_spelled_out_email() is the actual structural gate
        # (>=1 "at" AND >=2 "dot") that keeps ordinary prose from matching.
        _Recognizer(
            "EMAIL",
            pii_patterns.EMAIL_SPELLED_OUT_RE,
            lambda v: v.lower(),
            pii_validators.is_valid_spelled_out_email,
        ),
        _Recognizer("SSN", pii_patterns.SSN_RE, lambda v: v),
        _Recognizer("PAN", pii_patterns.PAN_RE, pii_validators.normalize_pan, pii_validators.is_valid_pan),
        _Recognizer(
            "AADHAAR", pii_patterns.AADHAAR_RE, pii_validators.normalize_aadhaar, pii_validators.is_aadhaar_shaped
        ),
        # JWT — see pii_patterns.JWT_RE's own comment: same compiled pattern
        # secrets.py's check_secrets() uses, imported not duplicated. No
        # normalization beyond identity — a JWT's three dot-separated
        # segments ARE the value; nothing to canonicalize.
        _Recognizer("JWT", pii_patterns.JWT_RE, lambda v: v),
        _Recognizer(
            "PHONE", pii_patterns.PHONE_CANDIDATE_RE, pii_validators.canonicalize_phone, pii_validators.is_valid_phone,
            context_gate=lambda text, m: pii_validators.phone_confidence(text, m.start(), m.end(), m.group(0)) != "low",
        ),
        # Obfuscated-shape PHONE candidates — same label, same downstream
        # validation (canonicalize_phone/is_valid_phone/phone_confidence) as
        # the ordinary PHONE recognizer directly above; only the candidate-
        # matching regex differs. See pii_patterns.py's PHONE_CHAR_SPACED_RE/
        # PHONE_SPELLED_OUT_RE for why each exists.
        _Recognizer(
            "PHONE", pii_patterns.PHONE_CHAR_SPACED_RE, pii_validators.canonicalize_phone, pii_validators.is_valid_phone,
            context_gate=lambda text, m: pii_validators.phone_confidence(text, m.start(), m.end(), m.group(0)) != "low",
        ),
        _Recognizer(
            "PHONE",
            pii_patterns.PHONE_SPELLED_OUT_RE,
            lambda v: pii_validators.canonicalize_phone(pii_validators.digit_words_to_digits(v)),
            pii_validators.is_valid_phone,
            context_gate=lambda text, m: pii_validators.phone_confidence(text, m.start(), m.end(), m.group(0)) != "low",
        ),
        # Luhn is the validity gate, not the regex. CREDIT_CARD_RE matches any
        # 13-16 digit run — order references and internal IDs share that shape
        # — so without a check digit every one of them was redacted as a
        # payment card. Real cards satisfy Luhn by construction, so this costs
        # no true-positive coverage.
        _Recognizer(
            "CREDIT_CARD", pii_patterns.CREDIT_CARD_RE,
            pii_validators.normalize_card, pii_validators.is_valid_card,
        ),
        _Recognizer("IP_ADDRESS", pii_patterns.IP_ADDRESS_RE, lambda v: v, pii_validators.is_valid_ipv4),
        _Recognizer(
            "DATE_OF_BIRTH", pii_patterns.DATE_OF_BIRTH_RE, lambda v: v,
            context_gate=lambda text, m: pii_validators.has_dob_context(text, m.start()),
        ),
    ]
    if settings.guardrail_employee_id_pattern:
        # Inserted first: an org-specific configured format is the most
        # confident signal available and should claim its span before any
        # generic pattern gets a chance to (e.g. an all-digit employee ID
        # scheme could otherwise be eaten by PHONE's candidate matching).
        recognizers.insert(
            0,
            _Recognizer(
                "EMPLOYEE_ID",
                pii_patterns.compile_employee_id_pattern(settings.guardrail_employee_id_pattern),
                lambda v: v.strip().upper(),
            ),
        )
    if settings.guardrail_bank_account_detection_enabled:
        recognizers.append(
            _Recognizer("BANK_ACCOUNT", pii_patterns.BANK_ACCOUNT_RE, lambda v: re.sub(r"\D", "", v), group=1)
        )
    recognizers.extend(_configured_detector_recognizers())
    return recognizers


def _configured_detector_recognizers() -> list["_Recognizer"]:
    """Admin-configured detectors for entities with no BUILT-IN recognizer
    (BANK_ACCOUNT/IFSC/CUSTOMER_ID today — see guardrail_policy/
    detector_capability.py's CONFIGURABLE_ENTITIES) — reads the SAME live
    policy cache (guardrail_policy/store.py, 5s TTL) every other PII check
    already reads for actions, just for a pattern instead. Appended, never
    inserted first: unlike guardrail_employee_id_pattern above (a single,
    deployment-wide setting an operator sets once, deliberately given first
    refusal), these are per-entity, admin-created-and-approved rows that
    should not pre-empt any of this module's own well-tested built-in
    shapes. A malformed or since-invalidated stored pattern is skipped
    (logged, not raised) rather than breaking every PII check in the
    process — validate_configuration()/test_pattern_safety() already gate
    this at write time, so a bad pattern reaching here would mean the
    stored data itself is inconsistent, not that this call should fail
    closed on an unrelated request."""
    from app.services.guardrail_policy import store
    from app.services.guardrail_policy.detector_capability import CONFIGURABLE_ENTITIES

    extra: list[_Recognizer] = []
    for row in store.get_active_policies("PII"):
        config = row.configuration or {}
        entity = (config.get("entity") or "").strip().upper()
        pattern = config.get("detector_pattern")
        if entity not in CONFIGURABLE_ENTITIES or not pattern:
            continue
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            logger.warning("pii.py: stored detector_pattern for %s failed to compile, skipping", entity)
            continue
        extra.append(_Recognizer(entity, compiled, lambda v: v.strip().upper()))
    return extra


def build_redaction_token(
    label: str, normalized_value: str, *, mode_override: str | None = None, custom_format: str | None = None,
    reveal_last: int | None = None,
) -> str:
    """The single place a `[REDACTED_*]`-style token gets constructed, in
    whichever mode (placeholder/hash/mask) guardrail_pii_mode selects —
    shared by this module's own regex-based redaction loop below AND
    gliner_check.py's span-based redaction, so there is exactly one
    redaction-token implementation in this codebase, not two.

    `mode_override`/`custom_format` are set only by redact_pii()'s
    policy-aware path (direction != None) — a Guardrail Policy Center PII
    row's resolved MASK vs REDACT action and its optional `redaction_format`
    override the process-wide `guardrail_pii_mode` setting for that one
    entity. `custom_format` only applies in "placeholder" mode (REDACT/
    BLOCK's full-replacement token) — MASK's partial reveal is always
    computed from the real value via _mask_token, never a static string, so
    a custom format can't silently disable that computation."""
    mode = mode_override or settings.guardrail_pii_mode
    if mode == "placeholder" and custom_format:
        return custom_format
    if mode == "mask":
        return _mask_token(label, normalized_value, reveal_last)
    build_token = _TOKEN_BUILDERS.get(mode, _placeholder_token)
    return build_token(label, normalized_value)


def preview_redaction(
    label: str, raw_value: str, *, action: str, reveal_last: int | None = None
) -> str:
    """What `raw_value` would look like under `action`, without changing any
    policy. Used by the Policy Copilot to simulate a proposed change on a
    SYNTHETIC value.

    It normalizes through the same recognizer the live pipeline would use, so
    the preview is the string the pipeline would actually produce — a preview
    computed from the raw match would differ from reality for every entity
    whose normalizer strips punctuation (PHONE, CREDIT_CARD, AADHAAR...),
    which is exactly the case where an admin most needs the digit count to be
    right.
    """
    label = label.strip().upper()
    normalized = raw_value
    for recognizer in _build_recognizers():
        if recognizer.label == label:
            try:
                normalized = recognizer.normalize(raw_value)
            except Exception:  # a synthetic value the normalizer can't parse
                normalized = raw_value
            break

    if action == "ALLOW":
        return raw_value
    if action == "FLAG":
        return raw_value
    if action == "MASK":
        return build_redaction_token(label, normalized, mode_override="mask", reveal_last=reveal_last)
    if action == "REDACT":
        return build_redaction_token(label, normalized, mode_override="placeholder")
    return "(request refused)"


def find_pii_labels(text: str) -> list[str]:
    """Detect-only sibling of redact_pii(): runs the exact same recognizer
    list (structure/validators/context-gates included) but returns which
    labels matched without substituting any text. For callers that need to
    know "does this text contain PII-shaped content" without mutating it —
    planner.py's _flag_suspicious_chunks() (retrieved-chunk visibility; the
    model still needs the real, already-RBAC-authorized value, so nothing
    here gets redacted) and the document-upload PII metadata tagging in
    routers/documents.py (stores label names only, never the matched
    spans). Deliberately NOT gated on settings.guardrail_redact_pii — that
    flag controls whether redaction (a mutation) happens, not whether
    detection (an audit/visibility signal) is allowed to run at all."""
    found: list[str] = []
    for rec in _build_recognizers():
        for match in rec.pattern.finditer(text):
            raw = match.group(rec.group)
            normalized = rec.normalize(raw)
            if rec.is_valid is not None and not rec.is_valid(normalized):
                continue
            if rec.context_gate is not None and not rec.context_gate(text, match):
                continue
            found.append(rec.label)
    return found


def _summarize(labels: list[str]) -> str:
    # Labels + counts only, e.g. "EMAIL×1, PHONE×2" — the raw matched values
    # never appear in this detail string. Earlier this returned e.g.
    # "Redacted: EMAIL 'jane@example.com'", which flowed straight into
    # services/monitoring/metrics.py::record_guardrail_event()'s in-memory
    # audit log (exposed via GET /admin/guardrail-analytics to any
    # analytics-viewing role, not just the user who typed it) — a real
    # raw-PII-in-audit-log leak, not just a hypothetical one.
    counts = Counter(labels)
    return ", ".join(f"{label}×{n}" for label, n in sorted(counts.items()))


def _resolve_match(
    rec: "_Recognizer", normalized: str, direction: str | None, mode_override: str | None = None,
    role: str | None = None,
) -> tuple[str, str | None, int | None]:
    """Returns (status, token, policy_version): status in {"allow", "flag",
    "redact", "block"}, token is the replacement string when status is
    "redact"/"block" (else None), and policy_version is the resolved custom
    row's version when one governed this match (else None — see
    PIIPolicyResolution.policy_version). direction=None (every existing
    caller except pipeline.py's two real enforcement call sites) skips
    policy resolution entirely — every match is unconditionally redacted via
    the process-wide guardrail_pii_mode setting, identical to this
    function's behavior before Guardrail Policy Center PII enforcement
    existed.

    mode_override forces the token format for that unconditional path only
    (direction=None). Its one caller is pipeline.py's scope re-check, which
    needs "placeholder" specifically: under the default "mask" mode a token
    is a partial reveal like "ja######.com", which is semantic noise to an
    embedding model and scores no better than the raw value it replaced
    (measured: 0.544 raw -> 0.549 masked, both below the 0.55 threshold),
    whereas "[REDACTED_EMAIL]" reads as a clean type marker (0.588, above
    it). Nothing about enforcement changes — only how a scoring-only copy
    is spelled."""
    if direction is None:
        return "redact", build_redaction_token(rec.label, normalized, mode_override=mode_override), None

    resolution = resolve_pii_policy(rec.label, role)
    if not resolution.enabled or "regex" not in resolution.detection_sources:
        return "allow", None, resolution.policy_version
    if resolution.dry_run:
        # Detect and log, per spec: DRY_RUN must never block/redact
        # production traffic, only record that it would have.
        return "flag", None, resolution.policy_version

    action = resolution.input_action if direction == "input" else resolution.output_action
    if action == "ALLOW":
        return "allow", None, resolution.policy_version
    if action == "FLAG":
        return "flag", None, resolution.policy_version
    if action == "MASK":
        return (
            "redact",
            build_redaction_token(
                rec.label, normalized, mode_override="mask", custom_format=resolution.redaction_format,
                reveal_last=resolution.reveal_last,
            ),
            resolution.policy_version,
        )
    if action in ("BLOCK", "ESCALATE"):
        # ESCALATE is treated as BLOCK this pass — a real per-message human
        # escalation workflow is a larger, separate feature (see the
        # approved plan's non-goals). Still redacted below as defense in
        # depth even though the caller will use the canned block reply, not
        # this returned text, once it sees action="block".
        return (
            "block",
            build_redaction_token(
                rec.label, normalized, mode_override="placeholder", custom_format=resolution.redaction_format
            ),
            resolution.policy_version,
        )
    # REDACT (also the fallback for any unrecognized action string, which
    # validate_action() should already have rejected before it ever reached
    # a persisted policy row — fail toward the safer full-replacement, never
    # toward leaving the match untouched)
    return (
        "redact",
        build_redaction_token(rec.label, normalized, mode_override="placeholder", custom_format=resolution.redaction_format),
        resolution.policy_version,
    )


@dataclass(frozen=True)
class PIIOccurrenceRecord:
    """One entity a real redact_pii()/check_with_gliner() call actually
    replaced — the raw span, its replacement, and enough metadata to audit
    who resolved it under which policy. Never constructed unless a caller
    opts in via `capture=` (both functions default to not collecting these —
    zero behavior/cost change for every existing caller).

    This is the ONLY place in the guardrail pipeline a matched raw PII value
    is allowed to survive past its own redaction call. Callers must not log
    it, put it in a GuardrailStep.detail, or write it into `messages.content`/
    `messages.trace` — see models/pii_occurrence.py's docstring for the one
    sanctioned destination (a column-isolated table, read by exactly one
    permissioned endpoint) and pipeline.py for how these records reach it.
    """

    entity_type: str
    raw_value: str
    sanitized_value: str
    detector: str  # "regex" (this module) | "gliner" (gliner_check.py)
    #: No existing recognizer determines this — see pii_occurrence.py's
    #: model docstring. Always None until a real per-country detector exists.
    country: str | None = None
    policy_version: int | None = None


def redact_pii(
    text: str, *, direction: Literal["input", "output"] | None = None, mode_override: str | None = None,
    role: str | None = None, capture: list["PIIOccurrenceRecord"] | None = None,
) -> tuple[str, GuardrailStep]:
    """direction=None (default) is the original, policy-unaware behavior —
    every existing caller (audit log sanitization, employee-PII masking,
    blocked-input storage, the evaluation harness) keeps using it unchanged.
    Only services/guardrails/pipeline.py's two real enforcement call sites
    pass direction="input"/"output", activating per-entity Guardrail Policy
    Center resolution (services/guardrail_policy/pii_policy.py).

    `capture`, when a list is passed, gets one PIIOccurrenceRecord appended
    per span actually redacted or blocked (never for "allow"/"flag" —
    nothing was replaced, there is no "original vs sanitized" pair to
    record). Every existing caller passes nothing here (default None) and is
    completely unaffected — this is additive, opt-in instrumentation for
    pipeline.py's raw-PII-capture path (see PIIOccurrenceRecord's own
    docstring for the security posture), not a change to what this function
    redacts or how."""
    if not settings.guardrail_redact_pii:
        return text, GuardrailStep(NAME, "pass", "Check disabled")

    redacted = text
    found_labels: list[str] = []  # redacted (REDACT/MASK) — labels only, never the matched value
    blocked_labels: list[str] = []  # BLOCK/ESCALATE
    flagged_labels: list[str] = []  # FLAG, or DRY_RUN detections — never redacted, never blocks

    for rec in _build_recognizers():

        def _replace(match: re.Match, rec: _Recognizer = rec) -> str:
            raw = match.group(rec.group)
            normalized = rec.normalize(raw)
            if rec.is_valid is not None and not rec.is_valid(normalized):
                return match.group(0)  # shaped like this type but failed validation — leave untouched
            if rec.context_gate is not None and not rec.context_gate(redacted, match):
                return match.group(0)  # e.g. PHONE at LOW confidence, or a date with no DOB context nearby

            status, token, policy_version = _resolve_match(rec, normalized, direction, mode_override, role)
            if status == "allow":
                return match.group(0)
            if status == "flag":
                flagged_labels.append(rec.label)
                return match.group(0)
            (blocked_labels if status == "block" else found_labels).append(rec.label)
            if capture is not None:
                capture.append(PIIOccurrenceRecord(
                    entity_type=rec.label, raw_value=raw, sanitized_value=token,
                    detector="regex", policy_version=policy_version,
                ))
            if rec.group == 0:
                return token
            # Only the captured span is PII (e.g. BANK_ACCOUNT's digits, not
            # the "account number:" label in front of them) — splice the
            # token into the full match, keeping the surrounding text.
            whole_start = match.start()
            g_start, g_end = match.span(rec.group)
            whole = match.group(0)
            return whole[: g_start - whole_start] + token + whole[g_end - whole_start :]

        redacted = rec.pattern.sub(_replace, redacted)

    if blocked_labels:
        return redacted, GuardrailStep(NAME, "block", f"Detected: {_summarize(blocked_labels)}")
    if found_labels:
        return redacted, GuardrailStep(NAME, "redact", f"Redacted: {_summarize(found_labels)}")
    if flagged_labels:
        return text, GuardrailStep(NAME, "pass", f"Flagged for review: {_summarize(flagged_labels)}")
    return text, GuardrailStep(NAME, "pass", "No PII detected")


@dataclass(frozen=True)
class DualText:
    """Pairs retrieved source content with its PII-redacted counterpart —
    the two representations services/reranking/pipeline.py,
    services/retrieval/search.py, and services/agents/planner.py keep
    strictly separated:

    - `raw`: the original, authorized document/chunk text. Allowed ONLY
      inside an authorized LLM/agent tool-execution context (the model
      already only ever sees chunks resolve_document_ids()/
      apply_category_policy() cleared for this user's request — RBAC has
      already run by the time this exists).
    - `display`: `redact_pii(raw)`'s output. The ONLY representation
      allowed into chat history, citations, API responses, reports,
      persisted database fields, or logs.

    A frozen dataclass (not a plain tuple/dict) so a caller has to name the
    field it wants explicitly — there's no positional/implicit way to grab
    the wrong one by accident.
    """

    raw: str
    display: str

    @classmethod
    def from_raw(cls, text: str) -> "DualText":
        return cls(raw=text, display=redact_pii(text)[0])
