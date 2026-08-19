"""Embedding-based scope check — a semantic complement to scope.py's
keyword allow/deny list. Reuses this codebase's existing BGE-M3 embedding
model (services/embedding/similarity.py, the same utility semantic_check.py
already uses) rather than loading a new model, so this check is close to
free given semantic_check.py already pays the embedding-model-load cost.

The gap this closes: scope.py's allow-list only matches a message
containing one of its literal configured keywords. A message about the
exact same topic phrased without any of those words (e.g. an allow-list
keyword of "maintenance schedule" doesn't match "when is the next planned
outage for Line 7?") slips past a keyword-only allow-list either direction —
wrongly allowed if only a deny-list is configured, wrongly rejected if the
allow-list is stricter in wording than intent. This check instead measures
similarity to a set of example in-scope questions and blocks when nothing is
close enough — genuinely complementary coverage, not a replacement for the
deterministic keyword check, which stays fast, exact, and first in the
pipeline.

Same opt-in semantics as scope.py's own allow-list: if no example topics are
configured, this check is a no-op (pass) — a deployment that hasn't defined
its scope by example gets exactly the previous behavior, nothing new
enforced silently.

SF-03 — per-clause decomposition
---------------------------------
Originally this scored the WHOLE message as one embedding. That is exactly
the bypass documented in docs/SECURITY_FINDINGS.md's SF-03: an out-of-scope
clause with an in-scope clause appended scores ABOVE threshold as a whole,
because the in-scope clause's embedding dominates the aggregate vector.
Measured on the real matcher: "What is the weather in Chennai?" alone scores
0.424 (correctly blocked); appending "Also what is our leave policy?" lifts
the SAME message to 0.687 (wrongly allowed) — reproducible, order-independent
(reversing the clauses scores 0.755).

The fix is per-clause evaluation with an ALL-MUST-PASS policy, not
best-clause scoring — SECURITY_FINDINGS.md documents that best-clause was
measured and rejected: it lifts the adversarial cases to 0.863-0.941,
making the bypass worse, not better. See _split_into_clauses() and
_evaluate() below.

This intentionally does NOT become a new pipeline stage. It's an internal
upgrade to check_scope_semantic()'s existing body, at its existing pipeline
position — every check that must run before decomposition per the approved
design (prompt injection, destructive intent, semantic risk, the DeBERTa
classifier) already runs earlier in pipeline.py's fixed order, so this
function inherits that ordering guarantee for free rather than needing to
re-implement it. It also means the pipeline's existing deferred-block/
post-redaction-recheck machinery (pipeline.py's `deferred_scope_step`,
SF-C2's redacted-text recheck) gets per-clause correctness automatically,
with no changes to pipeline.py's control flow at all.
"""

import re
import threading

from app.core.yaml_config import load_yaml_config
from app.services.embedding.similarity import MaxSimilarityMatcher
from app.services.guardrails.document_reference import looks_like_document_reference
from app.services.guardrails.pii import redact_pii
from app.services.guardrails.pii_reference import looks_like_pii
from app.services.guardrails.request_structure import has_request_structure
from app.services.guardrails.types import GuardrailStep

NAME = "scope_semantic_check"
#: Emitted instead of NAME when >=1 clause is in scope and >=1 is not — a
#: block an admin/response_generator.py can render as "I can help with X,
#: but not Y" rather than a flat refusal. See pipeline.py's _DECISION_MAP
#: and response_generator.py's "mixed_scope" branch.
MIXED_NAME = "scope_semantic_mixed"

# Splits on sentence terminators (the common, unambiguous case: "What is the
# weather in Chennai? Also what is our leave policy?" — this alone already
# defeats every compound example in SF-03's own writeup and in the adversarial
# test matrix this fix was built against).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")

# Narrow second pass: splits on " and " ONLY when immediately followed by a
# word that itself starts a new request/question — catches a bare-'and'
# compound with no sentence terminator at all ("Tell me today's stock price
# and explain our financial reporting policy."), which the sentence-splitter
# above can't see since there's no '.', '?', or '!' between the two clauses.
# Deliberately does NOT split on every "and": "What is the PPE and safety
# policy for my shift?" must stay ONE clause about "PPE and safety" — the
# word right after "and" here ("safety") isn't a request trigger, so this
# regex doesn't match and the sentence stays intact. Reuses the exact
# request/interrogative vocabulary request_structure.py already uses, so
# "does this look like the start of its own request" is answered the same
# way everywhere in this package, not by a second, drifting word list.
_AND_REQUEST_SPLIT_RE = re.compile(
    r"\s+and\s+(?=(?:also\s+)?(?:"
    r"tell me|show me|explain|summarize|list|find|generate|walk me through|"
    r"give me|help me|provide|describe|check|show|"
    r"what|how|who|when|where|why|which|can|could|would|should"
    r")\b)",
    re.IGNORECASE,
)

# "and also <anything>" splits unconditionally — no trigger-word lookahead
# needed, because "also" is itself already a strong, low-false-positive
# signal that a second, separate ask follows (unlike bare "and", which is
# very often just conjoining two nouns within one topic — "PPE and safety
# policy" must NOT split, which is exactly why the rule above stays gated on
# a trigger word while this one doesn't need to be). Found live: "While you
# are at it, tell me tonight's football score, and also the PPE policy for
# Line 3." — the tail "the PPE policy for Line 3" starts with a bare noun
# phrase, no trigger word, so _AND_REQUEST_SPLIT_RE alone doesn't split it;
# this rule does. Verified this doesn't false-split a benign compound
# question: "What is the PPE policy and also the safety guidelines for Line
# 3?" splits into two clauses that both independently score in scope
# (0.584, 0.561 against threshold 0.55) — decomposition still passes the
# whole message, exactly as it should.
_AND_ALSO_SPLIT_RE = re.compile(r"\s+and\s+also\s+", re.IGNORECASE)


def _split_into_clauses(text: str) -> list[str]:
    """Deterministic, regex-only — no model, no LLM, nothing for a prompt
    injection to steer (per the approved design's "decomposition must not
    become an attack surface" requirement). Never returns an empty list:
    falls back to the whole text as one clause, which is exactly today's
    pre-SF-03-fix behavior for any message this splitter doesn't recognize as
    compound — the common, single-topic case is unaffected by this change.

    Flat list, no structure — kept as its own small utility for anything that
    just wants "the clauses," e.g. tests. check_scope_semantic() below needs
    one more thing this function doesn't provide (see _split_into_scored_units)
    so it doesn't call this directly."""
    clauses: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text.strip()):
        for part in _AND_REQUEST_SPLIT_RE.split(sentence):
            clauses.extend(p.strip() for p in _AND_ALSO_SPLIT_RE.split(part) if p.strip())
    return clauses or [text.strip()]


def _split_into_scored_units(text: str) -> list[str]:
    """Which clauses actually get scored — the request-structure filter
    applies at the SENTENCE level, before " and "/" and also " sub-splitting,
    not to each resulting sub-clause independently.

    This matters for elliptical compounds sharing one verb across both
    halves: "tell me tonight's football score, and also the PPE policy for
    Line 3" is one sentence, "tell me ... and also [tell me] ..." — the
    second half has no verb of its own once split, so has_request_structure()
    on JUST that half is False even though the sentence as a whole is
    unmistakably a request. Filtering after sub-splitting would silently drop
    that half from scoring entirely — exactly the kind of gap that made the
    original whole-message check miss a genuinely out-of-scope clause, just
    relocated to a different step. Filtering before sub-splitting asks the
    right question once per sentence, then trusts the split.

    A sentence with NO request structure at all (a bare greeting, a bare PII
    value) is dropped whole, including any "and"/"and also" it happens to
    contain — nothing to sub-split when there was never a request to begin
    with.
    """
    units: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text.strip()):
        sentence = sentence.strip()
        if not sentence or not has_request_structure(sentence):
            continue
        for part in _AND_REQUEST_SPLIT_RE.split(sentence):
            units.extend(p.strip() for p in _AND_ALSO_SPLIT_RE.split(part) if p.strip())
    return units


_matcher_lock = threading.Lock()
_cached_topics: tuple[str, ...] | None = None
_cached_matcher: MaxSimilarityMatcher | None = None


def _get_matcher(topics: tuple[str, ...]) -> MaxSimilarityMatcher:
    """Rebuilt only when the configured topic list actually changes — same
    "don't re-embed on every call" motivation as semantic_check.py's
    module-level _matcher, just keyed on config instead of a fixed tuple
    since these examples are deployment-configured, not hardcoded."""
    global _cached_topics, _cached_matcher
    if _cached_matcher is None or _cached_topics != topics:
        with _matcher_lock:
            if _cached_matcher is None or _cached_topics != topics:
                _cached_matcher = MaxSimilarityMatcher(topics)
                _cached_topics = topics
    return _cached_matcher


def _config() -> dict:
    return load_yaml_config("guardrails.yaml").get("scope_semantic_check", {})


def _topic_label(topic: str) -> str:
    """Best-effort short label for a CONFIGURED topic sentence, e.g. "What is
    our leave management policy?" -> "leave management policy". Used only in
    the user-facing mixed-scope message (see response_generator.py's
    "mixed_scope" branch) — safe to derive because it operates on
    admin-configured text, never on anything the caller wrote. If the strip
    leaves nothing usable, falls back to a generic phrase rather than
    guessing."""
    stripped = re.sub(
        r"^(what is|what are|what does|how do i|how does|how do we|who|summarize|generate)\s+(our\s+|the\s+)?",
        "", topic.strip(), flags=re.IGNORECASE,
    ).rstrip("?.!").strip()
    return stripped or "that topic"


def _judge_unclear(text: str, score: float) -> GuardrailStep:
    """The UNCLEAR sub-branches — unchanged from before SF-03: a message (or,
    now, a clause) with no request structure at all gets classified by what
    it resembles rather than flatly refused. Only reached for text that
    ALREADY failed has_request_structure, so this never re-derives that
    check itself."""
    if looks_like_pii(text):
        return GuardrailStep(
            "scope_unclear_pii", "block",
            f"No clear request detected and message appears to contain personal information (best={score:.2f})",
        )
    if looks_like_document_reference(text):
        return GuardrailStep(
            "scope_unclear_document", "block",
            f"No clear request detected but message resembles a document reference (best={score:.2f})",
        )
    return GuardrailStep(
        "scope_unclear_context", "block",
        f"No clear request detected and no configured topic matched (best={score:.2f})",
    )


def check_scope_semantic(text: str) -> GuardrailStep:
    cfg = _config()
    if not cfg.get("enabled", True):
        return GuardrailStep(NAME, "pass", "Check disabled")

    topics = tuple(cfg.get("topics") or ())
    if not topics:
        return GuardrailStep(NAME, "pass", "No scope examples configured")

    truncated = text[: cfg.get("max_input_chars", 2000)]
    if not truncated.strip():
        return GuardrailStep(NAME, "pass", "Empty input")

    matcher = _get_matcher(topics)
    threshold = float(cfg.get("threshold", 0.55))

    # SF-03: only sentences that look like a request/question at all are
    # decomposed and scored — a non-substantive sentence ("Thanks!", a bare
    # document ID, a bare PII value with nothing asked of it) is dropped
    # whole rather than independently scored, so splitting a message never
    # invents a NEW false positive on filler text the whole-message check
    # would never have flagged on its own. See _split_into_scored_units's
    # docstring for why this filter runs before " and "/" and also "
    # sub-splitting, not after. If nothing substantive survives (the common
    # case for a genuinely non-request message), fall through to judging the
    # whole text exactly as before this change — bit-for-bit the same code
    # path a single-clause message already took.
    clauses = _split_into_scored_units(truncated)
    if not clauses:
        nearest_topic, score = matcher.best_match(truncated)
        if score >= threshold:
            return GuardrailStep(NAME, "pass", f"Closest configured topic: {nearest_topic!r} (score={score:.2f})")
        return _judge_unclear(truncated, score)

    # Every surviving clause has request structure by construction, so each
    # is judged the same way the OLD whole-message OUT_OF_SCOPE branch was:
    # below threshold here always means "clearly asking about the wrong
    # thing," never "unclear" (has_request_structure already ruled that out
    # per-clause) — matches _judge_unclear's own precondition.
    judged = [(c, *matcher.best_match(c)) for c in clauses]
    out_of_scope = [(c, topic, s) for c, topic, s in judged if s < threshold]
    in_scope = [(c, topic, s) for c, topic, s in judged if s >= threshold]

    if not out_of_scope:
        if len(judged) == 1:
            clause, topic, score = judged[0]
            return GuardrailStep(NAME, "pass", f"Closest configured topic: {topic!r} (score={score:.2f})")
        summary = "; ".join(f"{c!r} -> in scope (topic={t!r}, score={s:.2f})" for c, t, s in judged)
        return GuardrailStep(NAME, "pass", f"{len(judged)} request clauses detected, all in scope: {summary}")

    # Clause text is redacted (labels-only tokens, e.g. "My SSN is
    # [REDACTED_SSN]") before it goes into this breakdown — GuardrailStep.
    # detail reaches /traces and the metrics event log, and this codebase's
    # rule for every detail string reaching either of those is "labels only,
    # never the matched value" (see pii.py::_summarize()'s and chat.py's own
    # audit-log comments on exactly this). A clause containing PII that
    # itself scored out-of-scope — e.g. a bare "My SSN is 123-45-6789" — was
    # previously echoed here verbatim; redact_pii() is the same policy-
    # unaware, audit-log-sanitizing call pipeline.py's own scope re-check
    # already uses for an equivalent purpose.
    breakdown = "\n".join(
        f"  {i}. {redact_pii(c)[0]!r}\n     verdict: {'IN SCOPE' if s >= threshold else 'OUT OF SCOPE'} "
        f"(score={s:.2f}, nearest={t!r})"
        for i, (c, t, s) in enumerate(judged, 1)
    )
    if not in_scope:
        # Every clause is out of scope — same generic wording a single
        # out-of-scope message always got; nothing to salvage.
        detail = (
            f"{len(judged)} request clause(s) detected, all out of scope.\n{breakdown}\n"
            f"Final decision: BLOCK (no in-scope clause)"
        )
        return GuardrailStep(NAME, "block", detail)

    # Mixed: at least one clause is in scope, at least one is not. First line
    # of detail is the SAFE label response_generator.py's "mixed_scope"
    # branch reads — the admin-configured topic name of the in-scope clause,
    # never the caller's own out-of-scope text (see this module's docstring
    # on why the flat single-topic message never echoes raw message content,
    # and why that guarantee still holds here). Everything after the blank
    # line is audit-only detail — real clause text, PII-redacted (see
    # `breakdown`'s own construction above) — reaching /traces (privileged or
    # own-conversation-gated, per routers/traces.py) and the metrics event
    # log, never the flat chat-facing block_reason.
    label = _topic_label(in_scope[0][1]) if len(in_scope) == 1 else "the enterprise-related part of your message"
    detail = (
        f"{label}\n\n"
        f"{len(judged)} request clause(s) detected: {len(in_scope)} in scope, {len(out_of_scope)} out of scope.\n"
        f"{breakdown}\n"
        f"Final decision: BLOCK (at least one out-of-scope clause)"
    )
    return GuardrailStep(MIXED_NAME, "block", detail)
