"""services/guardrails/groundedness_check.py — the NLI cross-encoder
groundedness check. Mocks groundedness_check._get_model() directly (rather
than requiring the real cross-encoder to load per test), matching this
package's established convention.
"""

import pytest

from app.services.guardrails import groundedness_check


def _cfg(**overrides):
    base = {
        "enabled": True, "model_name": "cross-encoder/nli-deberta-v3-base", "contradiction_threshold": 0.5,
        "max_premise_chars": 4000, "max_reply_chars": 2000,
    }
    base.update(overrides)
    return base


class _FakeConfig:
    def __init__(self, id2label):
        self.id2label = id2label


class _FakeHFModel:
    def __init__(self, id2label):
        self.config = _FakeConfig(id2label)


class _FakeModel:
    """Mimics sentence_transformers.CrossEncoder's surface this module
    touches: .predict(pairs, apply_softmax=True) and .model.config.id2label."""

    def __init__(self, scores, id2label=None):
        self._scores = scores
        self.model = _FakeHFModel(id2label or {0: "contradiction", 1: "entailment", 2: "neutral"})
        self.calls = []

    def predict(self, pairs, apply_softmax=True):
        self.calls.append(pairs)
        return [self._scores]


@pytest.fixture(autouse=True)
def _reset_model_cache():
    groundedness_check._model = None
    yield
    groundedness_check._model = None


def _stub_model(monkeypatch, scores, id2label=None):
    fake = _FakeModel(scores, id2label)
    monkeypatch.setattr(groundedness_check, "_get_model", lambda model_name: fake)
    return fake


_SOURCES = [{"text": "SOP-MFG-101: lockout/tagout sequence for machine shutdown."}]


def test_no_sources_short_circuits_without_calling_the_model(monkeypatch):
    def _unexpected(model_name):
        raise AssertionError("_get_model must not be called when there are no sources")

    monkeypatch.setattr(groundedness_check, "_get_model", _unexpected)

    step = groundedness_check.check_groundedness("Some reply.", [])

    assert step.action == "pass"
    assert "no sources" in step.detail.lower()


def test_disabled_is_a_no_op(monkeypatch):
    monkeypatch.setattr(groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": _cfg(enabled=False)})

    def _unexpected(model_name):
        raise AssertionError("_get_model must not be called when the check is disabled")

    monkeypatch.setattr(groundedness_check, "_get_model", _unexpected)

    step = groundedness_check.check_groundedness("Some reply.", _SOURCES)

    assert step.action == "pass"
    assert "disabled" in step.detail.lower()


def test_never_blocks_even_on_high_contradiction_score(monkeypatch):
    monkeypatch.setattr(groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": _cfg()})
    _stub_model(monkeypatch, [0.95, 0.03, 0.02])  # contradiction, entailment, neutral

    step = groundedness_check.check_groundedness("A reply that contradicts its sources.", _SOURCES)

    assert step.action == "pass"
    assert "may contradict" in step.detail.lower()


def test_pass_detail_reflects_consistent_reply(monkeypatch):
    monkeypatch.setattr(groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": _cfg()})
    _stub_model(monkeypatch, [0.02, 0.9, 0.08])  # contradiction, entailment, neutral

    step = groundedness_check.check_groundedness("A well-supported reply.", _SOURCES)

    assert step.action == "pass"
    assert "consistent" in step.detail.lower()


def test_id2label_mapping_is_respected_regardless_of_index_order(monkeypatch):
    monkeypatch.setattr(groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": _cfg()})
    # Deliberately different index order than the "standard" mapping used
    # elsewhere in this file, to prove the check reads id2label rather than
    # assuming a fixed [contradiction, entailment, neutral] order.
    _stub_model(monkeypatch, [0.9, 0.05, 0.05], id2label={0: "entailment", 1: "contradiction", 2: "neutral"})

    step = groundedness_check.check_groundedness("A reply.", _SOURCES)

    assert "0.05" in step.detail  # contradiction score picked up from index 1, not index 0


def test_model_error_fails_open_by_default(monkeypatch):
    monkeypatch.setattr(groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": _cfg()})

    def _boom(model_name):
        class _Boom:
            def predict(self, pairs, apply_softmax=True):
                raise RuntimeError("model not loaded")

        return _Boom()

    monkeypatch.setattr(groundedness_check, "_get_model", _boom)

    step = groundedness_check.check_groundedness("Some reply.", _SOURCES)

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_premise_built_from_all_source_texts(monkeypatch):
    monkeypatch.setattr(groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": _cfg()})
    fake = _stub_model(monkeypatch, [0.02, 0.9, 0.08])

    groundedness_check.check_groundedness(
        "A reply.", [{"text": "First source."}, {"text": "Second source."}]
    )

    premise, hypothesis = fake.calls[0][0]
    assert "First source." in premise
    assert "Second source." in premise
