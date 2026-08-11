import hashlib
import hmac
import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "pii_redact"

_PATTERNS = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    # Leading `\b` (not `(?<!\w)`) would fail to match a "(" immediately after
    # whitespace — both sides of that gap are non-word characters, so `\b`
    # never holds there, and the regex engine starts the match one character
    # later at the first digit instead. That silently drops the leading "("
    # (and, for the same reason, a leading "+") from the match, so redaction
    # replaces "312) 555-0173" but leaves the opening "(" (or "+1") behind as
    # literal text next to the [REDACTED_PHONE] token — not a PII leak (the
    # digits are still gone), but a cosmetic artifact live-tested and fixed
    # here. `(?<!\w)` only constrains the character before the match, so it
    # doesn't have this same-side gap and correctly pulls the "(" in.
    "PHONE": re.compile(r"(?<!\w)(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
}


def _hash_token(label: str, value: str) -> str:
    # HMAC (not a bare hash) specifically so a low-entropy value like an SSN
    # or phone number isn't crackable via a rainbow table once salted with a
    # real secret — see guardrail_pii_hash_salt's own comment in config.py.
    digest = hmac.new(settings.guardrail_pii_hash_salt.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"[REDACTED_{label}_{digest[:8]}]"


def _placeholder_token(label: str, value: str) -> str:
    return f"[REDACTED_{label}]"


_TOKEN_BUILDERS = {"placeholder": _placeholder_token, "hash": _hash_token}


def redact_pii(text: str) -> tuple[str, GuardrailStep]:
    if not settings.guardrail_redact_pii:
        return text, GuardrailStep(NAME, "pass", "Check disabled")

    build_token = _TOKEN_BUILDERS.get(settings.guardrail_pii_mode, _placeholder_token)

    redacted = text
    found: list[tuple[str, str]] = []  # (label, matched text)
    # SSN/credit-card/phone patterns overlap in shape, so redact the more
    # specific ones first to avoid a looser pattern eating the match first.
    for label in ("EMAIL", "SSN", "CREDIT_CARD", "PHONE"):
        pattern = _PATTERNS[label]

        def _replace(match: re.Match, label=label) -> str:
            found.append((label, match.group(0)))
            return build_token(label, match.group(0))

        redacted = pattern.sub(_replace, redacted)

    if not found:
        return text, GuardrailStep(NAME, "pass", "No PII detected")

    matches = ", ".join(f"{label} {value!r}" for label, value in found)
    return redacted, GuardrailStep(NAME, "redact", f"Redacted: {matches}")


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
