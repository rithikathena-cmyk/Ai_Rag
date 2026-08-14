"""Deterministic, cheap signal for whether a message expresses a request or
question at all — independent of topic relevance (scope_semantic_check's
job) and independent of PII detection (pii.py's job). Used only to
distinguish OUT_OF_SCOPE ("I understood you, it's just not something I
cover") from UNCLEAR ("I can't tell what you're asking at all") when
semantic similarity is already below scope_semantic_check's threshold — see
that module for the call site.

Calibrated against 56 real examples spanning this app's manufacturing/HR/
engineering/executive domain (questions, imperative requests, sentence
fragments, statements, PII-only messages, off-topic questions) before
"check"/"show" were added to the bare-verb list — every other candidate verb
tested (update/submit/upload/search/get me/pull up/look up) changed zero
outcomes in that set and was deliberately left out to keep this list
evidence-driven rather than speculative. Re-measure against real traffic
before adding more.

Known, accepted limitation: short imperative requests phrased without any
of these markers (e.g. "delete row sql") will be misclassified as UNCLEAR
rather than OUT_OF_SCOPE. This only ever changes which refusal wording is
shown — both outcomes still block and neither ever reaches the agent — so
the failure mode is bounded to message accuracy, never a security gap.

Found live, fixed same pass: "Not sure what to ask" bare-word-matched
_INTERROGATIVE_RE on "what" and was misclassified OUT_OF_SCOPE — the phrase
expresses the ABSENCE of a question, not one. _UNCERTAINTY_RE excludes this
narrow, common pattern ("not sure/don't know/no idea + interrogative")
before the interrogative check runs. Doesn't handle every phrasing of
uncertainty, and a message combining an uncertainty phrase with a genuine
separate question elsewhere (rare) would still misclassify — same bounded
blast radius as every other limitation here: wording only, never security.
"""

import re

_QUESTION_MARK_RE = re.compile(r"\?")
_UNCERTAINTY_RE = re.compile(
    r"\b(not sure|don'?t know|no idea|unsure|not certain|can'?t decide)\s+(what|how|who|when|where|why|which)\b",
    re.IGNORECASE,
)
_INTERROGATIVE_RE = re.compile(r"\b(what|how|who|when|where|why|which|can|could|would|should)\b", re.IGNORECASE)
_REQUEST_PHRASE_RE = re.compile(
    r"\b(tell me|show me|explain|summarize|list|find|generate|walk me through|"
    r"give me|help me|provide|describe|check|show)\b",
    re.IGNORECASE,
)


def has_request_structure(text: str) -> bool:
    if _UNCERTAINTY_RE.search(text):
        return False
    return bool(_QUESTION_MARK_RE.search(text) or _INTERROGATIVE_RE.search(text) or _REQUEST_PHRASE_RE.search(text))
