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
"""

import threading

from app.core.yaml_config import load_yaml_config
from app.services.embedding.similarity import MaxSimilarityMatcher
from app.services.guardrails.document_reference import looks_like_document_reference
from app.services.guardrails.pii_reference import looks_like_pii
from app.services.guardrails.request_structure import has_request_structure
from app.services.guardrails.types import GuardrailStep

NAME = "scope_semantic_check"

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

    nearest_topic, score = _get_matcher(topics).best_match(truncated)
    threshold = float(cfg.get("threshold", 0.55))
    if score < threshold:
        # Below-threshold similarity alone doesn't mean "unrelated" — a
        # clearly-phrased question about the wrong subject (OUT_OF_SCOPE) and
        # a bare reference/fragment with no topic to even compare (UNCLEAR)
        # can score nearly identically (verified: "How do I delete a row in
        # SQL?" at 0.52 vs "My email is john@example.com" at 0.50 — within
        # 0.03 of each other for very different reasons). has_request_structure()
        # is the actual discriminator; the PII/document-reference checks below
        # only pick which UNCLEAR wording applies, never whether it's UNCLEAR.
        if has_request_structure(truncated):
            return GuardrailStep(
                NAME, "block", f"No configured topic matched closely enough (best={score:.2f} vs {nearest_topic!r})"
            )
        if looks_like_pii(truncated):
            return GuardrailStep(
                "scope_unclear_pii", "block",
                f"No clear request detected and message appears to contain personal information (best={score:.2f})",
            )
        if looks_like_document_reference(truncated):
            return GuardrailStep(
                "scope_unclear_document", "block",
                f"No clear request detected but message resembles a document reference (best={score:.2f})",
            )
        return GuardrailStep(
            "scope_unclear_context", "block",
            f"No clear request detected and no configured topic matched (best={score:.2f})",
        )
    return GuardrailStep(NAME, "pass", f"Closest configured topic: {nearest_topic!r} (score={score:.2f})")
