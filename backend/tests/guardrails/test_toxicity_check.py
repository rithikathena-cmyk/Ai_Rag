"""services/guardrails/toxicity_check.py — the toxic-bert multi-label
toxicity classifier. Mocks toxicity_check._get_pipeline() directly (rather
than requiring the real transformers model to load per test), matching this
package's established convention — see test_deberta_injection_check.py.
"""

import pytest

from app.services.guardrails import toxicity_check


def _cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.7, "max_input_chars": 2000}
    base.update(overrides)
    return base


class _FakePipeline:
    def __init__(self, scores):
        self._scores = scores  # list of {"label": ..., "score": ...}
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return [self._scores]  # top_k=None shape: one list of label/score dicts per input


@pytest.fixture(autouse=True)
def _reset_pipeline_cache():
    toxicity_check._pipeline = None
    yield
    toxicity_check._pipeline = None


def _stub_pipeline(monkeypatch, scores):
    fake = _FakePipeline(scores)
    monkeypatch.setattr(toxicity_check, "_get_pipeline", lambda model_name: fake)
    return fake


_CLEAN_SCORES = [
    {"label": "toxic", "score": 0.01},
    {"label": "severe_toxic", "score": 0.0},
    {"label": "obscene", "score": 0.01},
    {"label": "threat", "score": 0.0},
    {"label": "insult", "score": 0.01},
    {"label": "identity_hate", "score": 0.0},
]

_TOXIC_SCORES = [
    {"label": "toxic", "score": 0.97},
    {"label": "severe_toxic", "score": 0.2},
    {"label": "obscene", "score": 0.9},
    {"label": "threat", "score": 0.05},
    {"label": "insult", "score": 0.85},
    {"label": "identity_hate", "score": 0.1},
]


def test_disabled_is_a_no_op_and_never_builds_the_pipeline(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=False)})

    def _unexpected(model_name):
        raise AssertionError("_get_pipeline must not be called when the check is disabled")

    monkeypatch.setattr(toxicity_check, "_get_pipeline", _unexpected)

    step = toxicity_check.check_toxicity("hello")

    assert step.action == "pass"
    assert "disabled" in step.detail.lower()


def test_empty_input_short_circuits_without_calling_the_pipeline(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg()})

    def _unexpected(model_name):
        raise AssertionError("_get_pipeline must not be called for empty input")

    monkeypatch.setattr(toxicity_check, "_get_pipeline", _unexpected)

    step = toxicity_check.check_toxicity("   ")

    assert step.action == "pass"


def test_pass_on_clean_message(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg()})
    _stub_pipeline(monkeypatch, _CLEAN_SCORES)

    step = toxicity_check.check_toxicity("What is the annual leave accrual rate?")

    assert step.action == "pass"
    assert step.name == "toxicity_check"


def test_block_when_any_label_exceeds_threshold(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg()})
    _stub_pipeline(monkeypatch, _TOXIC_SCORES)

    step = toxicity_check.check_toxicity("some genuinely abusive message")

    assert step.action == "block"
    assert "toxic" in step.detail.lower()


def test_below_threshold_scores_do_not_block(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(score_threshold=0.99)})
    _stub_pipeline(monkeypatch, _TOXIC_SCORES)

    step = toxicity_check.check_toxicity("a borderline message")

    assert step.action == "pass"


def test_input_truncated_to_max_input_chars(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(max_input_chars=10)})
    fake = _stub_pipeline(monkeypatch, _CLEAN_SCORES)

    toxicity_check.check_toxicity("a very long message that exceeds the configured max_input_chars limit")

    assert len(fake.calls[0]) == 10


def test_pipeline_error_fails_open_by_default(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg()})

    def _boom(model_name):
        class _Boom:
            def __call__(self, text):
                raise RuntimeError("model not loaded")

        return _Boom()

    monkeypatch.setattr(toxicity_check, "_get_pipeline", _boom)

    step = toxicity_check.check_toxicity("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_pipeline_error_fails_closed_when_configured(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(fail_closed=True)})

    def _boom(model_name):
        class _Boom:
            def __call__(self, text):
                raise RuntimeError("model not loaded")

        return _Boom()

    monkeypatch.setattr(toxicity_check, "_get_pipeline", _boom)

    step = toxicity_check.check_toxicity("hello")

    assert step.action == "block"
    assert "failed closed" in step.detail.lower()
