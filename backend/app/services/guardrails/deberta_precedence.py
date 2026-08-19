"""A narrow, evidence-based precedence rule for one specific, characterized
deberta_injection_check.py false-positive pattern — NOT a general "trust PII
over the classifier" rule, and NOT a threshold change.

The false positive, characterized directly against the live model before
writing this rule: certain PII-disclosure SENTENCE TEMPLATES ("My credit
card number is X.", "My PAN is X, file my return.", "My date of birth is X,
verify me.") score as injection at 0.98-1.00 confidence regardless of the
actual value — five different real card numbers all triggered it identically,
and a version of the same sentence with the value redacted to a placeholder
STILL triggered it at 1.00. Both facts together prove the signal is entangled
with the surrounding phrase shape, not the value — so redacting the value and
re-checking (the same pattern pipeline.py already uses to clear a deferred
scope_semantic_check block) does NOT work here and was deliberately not
reused for this rule.

The override in should_override_deberta_block() below fires ONLY when EVERY
non-trivial sentence in the flagged message contains at least one entity
pii.py's deterministic recognizer actually VALIDATES (find_pii_labels() —
already Luhn/checksum/context-gated, never a bare shape match). This is the
operational stand-in for "the flagged span overlaps the validated PII":
DeBERTa is a whole-text classifier with no token-level attribution to compare
a span against, so instead of trusting one span, this requires the ENTIRE
flagged text to be accounted for by a PII disclosure. A message with even one
sentence that isn't a PII disclosure never qualifies — that sentence could be
exactly the kind of paraphrased injection attempt DeBERTa exists to catch
that check_prompt_injection()'s fixed pattern list misses, and this rule must
never suppress a genuine detection to avoid that outcome. See
test_deberta_precedence.py for the adversarial cases this was checked
against, including genuine injection deliberately combined with real PII in
the same message.

This function is only ever called on text that has ALREADY passed
check_prompt_injection() — pipeline.py only reaches check_with_deberta() once
every earlier check, including that one, has already returned "pass" on this
exact text. This module does not re-derive that; it is a precondition of
where it is called from, not something checked here.
"""

from __future__ import annotations

import re

from app.services.guardrails.pii import find_pii_labels

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")


def should_override_deberta_block(text: str) -> bool:
    """True only when every non-trivial sentence in `text` contains at least
    one validated PII entity — see module docstring for the full reasoning
    and why this is deliberately conservative (fails closed to "do not
    override" whenever there's any ambiguity)."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return False
    return all(find_pii_labels(sentence) for sentence in sentences)
