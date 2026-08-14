"""Read-only, non-mutating probe for whether a message merely LOOKS
PII-shaped — used only to select response wording inside
scope_semantic_check's UNCLEAR branch, never to make a block/pass decision.
The real, authoritative PII detection/redaction (pii.py's redact_pii(),
presidio_check.py) is completely unchanged and still runs at its existing
pipeline position; this module has no relationship to it beyond reusing the
same compiled regex objects read-only. A false positive here only picks
slightly different clarification wording, never a different security
outcome, so a plain structural regex match — no validators, no context
gates, no mutation — is sufficient rigor for this job. Deliberately does NOT
call pii.py.redact_pii() or import anything mutating from that module.
"""

from app.services.guardrails import pii_patterns

# PHONE_CANDIDATE_RE deliberately excluded — pii_patterns.py's own docstring
# calls it a "candidate matcher only" that "will match '2026' or '12345' as
# candidates," meaningless without pii_validators.is_valid_phone()'s digit-
# count/prefix validation, which this read-only probe intentionally doesn't
# reuse (see module docstring — no validators, no mutation, structural
# match only). Verified live: "GEN-EXEC-KPI-101" tripped it via the bare
# "101" before this exclusion was added. EMAIL/SSN/PAN are safe to use bare
# — pii_patterns.py documents their regex as "the complete structural
# definition" with no separate validation stage.


def looks_like_pii(text: str) -> bool:
    return bool(
        pii_patterns.EMAIL_RE.search(text)
        or pii_patterns.SSN_RE.search(text)
        or pii_patterns.PAN_RE.search(text)
    )
