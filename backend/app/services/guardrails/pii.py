import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from app.core.config import settings
from app.services.guardrails import pii_patterns, pii_validators
from app.services.guardrails.types import GuardrailStep

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


_TOKEN_BUILDERS: dict[str, Callable[[str, str], str]] = {"placeholder": _placeholder_token, "hash": _hash_token}


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
        _Recognizer("SSN", pii_patterns.SSN_RE, lambda v: v),
        _Recognizer("PAN", pii_patterns.PAN_RE, pii_validators.normalize_pan, pii_validators.is_valid_pan),
        _Recognizer(
            "AADHAAR", pii_patterns.AADHAAR_RE, pii_validators.normalize_aadhaar, pii_validators.is_aadhaar_shaped
        ),
        _Recognizer(
            "PHONE", pii_patterns.PHONE_CANDIDATE_RE, pii_validators.canonicalize_phone, pii_validators.is_valid_phone,
            context_gate=lambda text, m: pii_validators.phone_confidence(text, m.start(), m.end(), m.group(0)) != "low",
        ),
        _Recognizer("CREDIT_CARD", pii_patterns.CREDIT_CARD_RE, lambda v: re.sub(r"\D", "", v)),
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
    return recognizers


def redact_pii(text: str) -> tuple[str, GuardrailStep]:
    if not settings.guardrail_redact_pii:
        return text, GuardrailStep(NAME, "pass", "Check disabled")

    build_token = _TOKEN_BUILDERS.get(settings.guardrail_pii_mode, _placeholder_token)
    redacted = text
    found_labels: list[str] = []  # labels only, never the matched value — see module-level note below

    for rec in _build_recognizers():

        def _replace(match: re.Match, rec: _Recognizer = rec) -> str:
            raw = match.group(rec.group)
            normalized = rec.normalize(raw)
            if rec.is_valid is not None and not rec.is_valid(normalized):
                return match.group(0)  # shaped like this type but failed validation — leave untouched
            if rec.context_gate is not None and not rec.context_gate(redacted, match):
                return match.group(0)  # e.g. PHONE at LOW confidence, or a date with no DOB context nearby
            found_labels.append(rec.label)
            token = build_token(rec.label, normalized)
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

    if not found_labels:
        return text, GuardrailStep(NAME, "pass", "No PII detected")

    # Labels + counts only, e.g. "EMAIL×1, PHONE×2" — the raw matched values
    # never appear in this detail string. Earlier this returned e.g.
    # "Redacted: EMAIL 'jane@example.com'", which flowed straight into
    # services/monitoring/metrics.py::record_guardrail_event()'s in-memory
    # audit log (exposed via GET /admin/guardrail-analytics to any
    # analytics-viewing role, not just the user who typed it) — a real
    # raw-PII-in-audit-log leak, not just a hypothetical one.
    counts = Counter(found_labels)
    summary = ", ".join(f"{label}×{n}" for label, n in sorted(counts.items()))
    return redacted, GuardrailStep(NAME, "redact", f"Redacted: {summary}")


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
