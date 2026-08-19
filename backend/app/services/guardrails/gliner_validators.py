"""Specialized validators for GLiNER candidate spans — GLiNER's own label
match + confidence score is never, on its own, sufficient to accept a
candidate as real PII (see gliner_check.py's module docstring). Each
function here takes a candidate span's matched text and returns True if it
should be VETOED (rejected despite GLiNER's own score) for a specific,
evidence-based reason — never a general "does this look risky" heuristic.

The concrete, motivating case: GLiNER's "government-issued identification
number" label previously scored 0.62-0.77 against this app's own
STF-MFG-41220-style employee-ID format, above the 0.6 default threshold —
a real, live false positive (it blocked an ordinary incident-report query
outright) that five different label-wording attempts failed to fix (see
guardrails.yaml's gliner_check.enabled comment history). Re-wording the
label is a dead end already proven not to work; a structural check against
this deployment's own configured ID format is an independent signal label
wording can't provide, which is why this module exists as its own veto
layer rather than another label-tuning attempt.

Deliberately a short list of exclusion checks, not a general-purpose
"is this plausible" scorer — add a new entry only for another concrete,
evidence-based false positive, the same way this one was found (a live
query that was wrongly blocked), not a speculative addition."""

import re
from typing import Callable

from app.core.config import settings
from app.services.guardrails import pii_patterns

# label -> list of (candidate text -> True means "veto this candidate")
# checks. A label with no entry here has no exclusion checks at all.
_VETO_CHECKS: dict[str, list[Callable[[str], bool]]] = {}


def _is_configured_employee_id(candidate_text: str) -> bool:
    """True if `candidate_text` matches this deployment's own configured
    employee-ID pattern (the same setting pii.py's own EMPLOYEE_ID
    recognizer uses) — an org-specific identifier format is a completely
    different kind of thing than a government-issued ID, and this
    deployment already has an authoritative definition of what its own
    employee IDs look like, independent of anything GLiNER scored."""
    pattern = settings.guardrail_employee_id_pattern
    if not pattern:
        return False
    import re

    return bool(re.fullmatch(pattern, candidate_text.strip(), re.IGNORECASE))


def _is_internal_reference_id(candidate_text: str) -> bool:
    """True if `candidate_text` matches this deployment's own internal
    reference-ID shape (settings.guardrail_internal_id_pattern) — a second,
    independent veto signal alongside _is_configured_employee_id above, for
    the same "an org-specific ID format is a different kind of thing than a
    government-issued one" reason, but NOT gated on whether employee IDs are
    also tracked as a distinct PII type (see that setting's own docstring in
    config.py for why the two are deliberately decoupled). Live-verified
    false positive this closes: "Incident STF-MFG-41220 was raised - what is
    the follow-up procedure?" — see
    tests/security/pii/test_pii_entities.py's PII-FP-01."""
    pattern = settings.guardrail_internal_id_pattern
    if not pattern:
        return False
    return bool(re.fullmatch(pattern, candidate_text.strip(), re.IGNORECASE))


def _is_deterministic_ssn(candidate_text: str) -> bool:
    """True if `candidate_text` is exactly a well-formed SSN pii.py's own
    deterministic SSN_RE recognizer already covers precisely (see
    pii_patterns.py) — a plain, standard-format SSN is not the "PII shape
    regex doesn't already cover" case gliner_check.py's own module docstring
    describes this label as being FOR (see that module: "natural-language
    descriptions of the identifier SHAPES pii.py's regex ... don't already
    cover"). Vetoing it here does not reduce detection: redact_pii() runs
    immediately after gliner_check() in pipeline.py's fixed check order and
    independently matches the exact same span with its own dedicated,
    format-validated recognizer — this only stops GLiNER's broader label
    from claiming (and misattributing, in the trace) a span the
    deterministic layer already owns. Live-verified:
    "My social security number is 123-45-6789, check my file." — see
    tests/security/pii/test_pii_entities.py's PII-SSN-01/PII-SSN-04."""
    return bool(pii_patterns.SSN_RE.fullmatch(candidate_text.strip()))


def _is_a_bare_category_mention(candidate_text: str) -> bool:
    """True if `candidate_text` contains no digit at all — i.e. GLiNER
    matched the WORDS describing an identifier category ("social security
    number", "passport number") rather than an actual value. Live-measured
    on PII-SSN-01's own input, "My social security number is 123-45-6789,
    check my file.": GLiNER returns two SEPARATE, non-overlapping
    candidates for this one label — "social security number" (score 0.73)
    and "123-45-6789" (score 0.71) — so vetoing the digit-value candidate
    alone (_is_deterministic_ssn above) was not sufficient; the bare-phrase
    candidate still counted as an "acting" redaction and kept this check
    ahead of pii.py's own SSN recognizer in the trace. A category mention
    with no digits redacts nothing sensitive — the words themselves aren't
    personal data — so there is no protection lost by rejecting it. Every
    real identifier VALUE this label is meant to catch (a spelled-out
    passport number, an unusually-formatted SSN) contains at least one
    digit; a candidate with none is definitionally not a value. Scoped to
    this one label, not applied generally, for the same reason every other
    check in this module is scoped narrowly (see module docstring)."""
    return not any(ch.isdigit() for ch in candidate_text)


_VETO_CHECKS["government-issued identification number such as a social security number or passport number"] = [
    _is_configured_employee_id,
    _is_internal_reference_id,
    _is_deterministic_ssn,
    _is_a_bare_category_mention,
]


def is_vetoed(label: str, candidate_text: str) -> bool:
    """True if any registered veto check for this label rejects the
    candidate. A label with no registered checks is never vetoed here —
    absence of a check is not evidence of safety, it just means no
    concrete false positive has been found for that label yet."""
    return any(check(candidate_text) for check in _VETO_CHECKS.get(label, []))
