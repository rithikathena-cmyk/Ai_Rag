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
