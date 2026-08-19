"""services/guardrails/scope_semantic_check.py — the embedding-similarity
scope check. Stubs MaxSimilarityMatcher.best_match() directly (via
scope_semantic_check._get_matcher()) rather than requiring the real BGE-M3
model to load per test, matching test_semantic_check.py's own convention for
the underlying matcher.
"""

import pytest

from app.services.guardrails import scope_semantic_check


def _cfg(**overrides):
    base = {"enabled": True, "topics": ["how do I request time off"], "threshold": 0.55, "max_input_chars": 2000}
    base.update(overrides)
    return base


class _FakeMatcher:
    def __init__(self, nearest, score):
        self._result = (nearest, score)

    def best_match(self, text):
        return self._result


@pytest.fixture(autouse=True)
def _reset_matcher_cache():
    scope_semantic_check._cached_matcher = None
    scope_semantic_check._cached_topics = None
    yield
    scope_semantic_check._cached_matcher = None
    scope_semantic_check._cached_topics = None


def _stub_matcher(monkeypatch, nearest, score):
    monkeypatch.setattr(scope_semantic_check, "_get_matcher", lambda topics: _FakeMatcher(nearest, score))


def test_disabled_is_a_no_op(monkeypatch):
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(enabled=False)})

    step = scope_semantic_check.check_scope_semantic("anything at all")

    assert step.action == "pass"
    assert "disabled" in step.detail.lower()


def test_no_topics_configured_is_a_no_op(monkeypatch):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(topics=[])}
    )

    step = scope_semantic_check.check_scope_semantic("anything at all")

    assert step.action == "pass"
    assert "no scope examples" in step.detail.lower()


def test_empty_input_short_circuits(monkeypatch):
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg()})

    step = scope_semantic_check.check_scope_semantic("   ")

    assert step.action == "pass"


def test_pass_when_similarity_meets_threshold(monkeypatch):
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg()})
    _stub_matcher(monkeypatch, "how do I request time off", 0.81)

    step = scope_semantic_check.check_scope_semantic("how do I file for vacation leave")

    assert step.action == "pass"


def test_block_when_nothing_matches_closely_enough(monkeypatch):
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg()})
    _stub_matcher(monkeypatch, "how do I request time off", 0.10)

    step = scope_semantic_check.check_scope_semantic("what's the weather like today")

    assert step.action == "block"


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(threshold=0.05)}
    )
    _stub_matcher(monkeypatch, "how do I request time off", 0.10)

    step = scope_semantic_check.check_scope_semantic("borderline message")

    assert step.action == "pass"


def test_matcher_cache_rebuilt_only_when_topics_change(monkeypatch):
    calls = []

    class _CountingMatcher:
        def __init__(self, topics):
            calls.append(topics)

        def best_match(self, text):
            return ("x", 0.9)

    monkeypatch.setattr(scope_semantic_check, "MaxSimilarityMatcher", _CountingMatcher)
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(topics=["a", "b"])}
    )

    scope_semantic_check.check_scope_semantic("first call")
    scope_semantic_check.check_scope_semantic("second call")

    assert len(calls) == 1  # not rebuilt when topics are unchanged


# --------------------------------------------------------------------------
# SF-03 — clause splitting (pure, no model, no config)
# --------------------------------------------------------------------------

class TestSplitIntoClauses:
    """_split_into_clauses: the flat splitter. Pure regex — every case here
    should hold regardless of what topics/threshold are configured."""

    def test_sentence_terminators_split(self):
        assert scope_semantic_check._split_into_clauses(
            "What is the weather in Chennai? Also what is our leave policy?"
        ) == ["What is the weather in Chennai?", "Also what is our leave policy?"]

    def test_bare_and_splits_only_before_a_request_trigger(self):
        assert scope_semantic_check._split_into_clauses(
            "Tell me today's stock price and explain our financial reporting policy."
        ) == ["Tell me today's stock price", "explain our financial reporting policy."]

    def test_bare_and_does_not_split_a_noun_conjunction(self):
        # "safety" isn't a request trigger — this must stay ONE clause about
        # "PPE and safety", not fragment into a nonsense first half.
        assert scope_semantic_check._split_into_clauses(
            "What is the PPE and safety policy for my shift?"
        ) == ["What is the PPE and safety policy for my shift?"]

    def test_and_also_splits_unconditionally(self):
        assert scope_semantic_check._split_into_clauses(
            "While you are at it, tell me tonight's football score, and also the PPE policy for Line 3."
        ) == ["While you are at it, tell me tonight's football score,", "the PPE policy for Line 3."]

    def test_single_topic_message_is_one_clause(self):
        assert scope_semantic_check._split_into_clauses("What is the leave policy for new hires?") == [
            "What is the leave policy for new hires?"
        ]

    def test_never_returns_empty(self):
        assert scope_semantic_check._split_into_clauses("   ") == [""]


class TestSplitIntoScoredUnits:
    """_split_into_scored_units: what actually gets scored — the request-
    structure filter applies per SENTENCE, before and/and-also sub-splitting,
    not to each resulting sub-clause independently. See its own docstring for
    why (elliptical compounds sharing one verb across both halves)."""

    def test_non_request_sentence_is_dropped_whole(self):
        # "Thanks!" has no request structure and must not be independently
        # scored (and, before this filter existed, would have been wrongly
        # classified UNCLEAR) — only the real question survives.
        assert scope_semantic_check._split_into_scored_units("Thanks! What is our leave policy?") == [
            "What is our leave policy?"
        ]

    def test_elliptical_second_half_is_still_scored(self):
        # "the PPE policy for Line 3." has no verb of its own — it inherits
        # "tell me" from the first half. Dropping it (scoring only the first
        # half) is exactly the gap that let a mixed message slip through.
        units = scope_semantic_check._split_into_scored_units(
            "While you are at it, tell me tonight's football score, and also the PPE policy for Line 3."
        )
        assert units == ["While you are at it, tell me tonight's football score,", "the PPE policy for Line 3."]

    def test_wholly_non_request_message_yields_no_units(self):
        # Falls back to whole-text judging in check_scope_semantic — this
        # function itself just reports nothing survived.
        assert scope_semantic_check._split_into_scored_units("My email is jane.doe@example.com") == []


def test_topic_label_strips_interrogative_preamble():
    assert scope_semantic_check._topic_label("What is our leave management policy?") == "leave management policy"
    assert scope_semantic_check._topic_label("How do I submit an engineering change request?") == (
        "submit an engineering change request"
    )


def test_topic_label_falls_back_when_stripped_to_nothing():
    assert scope_semantic_check._topic_label("???") == "that topic"


# --------------------------------------------------------------------------
# SF-03 — the mixed-scope verdict, with a matcher stubbed per-clause text
# (not a fixed tuple, since the whole point under test is that DIFFERENT
# clauses of the same message score differently)
# --------------------------------------------------------------------------

class _PerClauseFakeMatcher:
    """Maps exact clause text to a (nearest_topic, score) pair — a fixed-
    tuple fake can't exercise decomposition, since every clause would score
    identically no matter how the message was split."""

    def __init__(self, scores: dict):
        self._scores = scores

    def best_match(self, text):
        return self._scores[text]


def test_mixed_message_blocks_with_the_mixed_step_name(monkeypatch):
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg()})
    monkeypatch.setattr(
        scope_semantic_check, "_get_matcher",
        lambda topics: _PerClauseFakeMatcher({
            "What is the weather in Chennai?": ("how do I request time off", 0.42),
            "Also what is our leave policy?": ("how do I request time off", 0.91),
        }),
    )

    step = scope_semantic_check.check_scope_semantic(
        "What is the weather in Chennai? Also what is our leave policy?"
    )

    assert step.name == scope_semantic_check.MIXED_NAME
    assert step.action == "block"
    # First line of detail is the safe label response_generator.py reads —
    # never the caller's own out-of-scope clause text. _topic_label() strips
    # the interrogative preamble off the raw topic string.
    assert step.detail.splitlines()[0] == "request time off"
    assert "OUT OF SCOPE" in step.detail
    assert "IN SCOPE" in step.detail


def test_all_clauses_in_scope_passes_with_the_plain_name(monkeypatch):
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg()})
    monkeypatch.setattr(
        scope_semantic_check, "_get_matcher",
        lambda topics: _PerClauseFakeMatcher({
            "What is our leave policy?": ("how do I request time off", 0.91),
            "Also what are the PPE requirements?": ("how do I request time off", 0.88),
        }),
    )

    step = scope_semantic_check.check_scope_semantic(
        "What is our leave policy? Also what are the PPE requirements?"
    )

    assert step.name == scope_semantic_check.NAME
    assert step.action == "pass"


def test_all_clauses_out_of_scope_uses_the_plain_block_name(monkeypatch):
    # Nothing to salvage — same generic wording a single out-of-scope
    # message always got, not the mixed-scope message (there's no in-scope
    # part to offer instead).
    monkeypatch.setattr(scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg()})
    monkeypatch.setattr(
        scope_semantic_check, "_get_matcher",
        lambda topics: _PerClauseFakeMatcher({
            "What is the weather in Chennai?": ("how do I request time off", 0.42),
            "Also who won the match?": ("how do I request time off", 0.30),
        }),
    )

    step = scope_semantic_check.check_scope_semantic("What is the weather in Chennai? Also who won the match?")

    assert step.name == scope_semantic_check.NAME
    assert step.action == "block"
