"""services/guardrails/pipeline.py — wiring for scope_semantic_check,
input-only, positioned after deberta_injection_check and before
toxicity_check in run_input_guardrails(). Mirrors
test_pipeline_deberta_wiring.py's conventions.
"""

from app.services.guardrails import pipeline, scope_semantic_check


def _cfg(**overrides):
    base = {"enabled": True, "topics": ["how do I request time off"], "threshold": 0.55, "max_input_chars": 2000}
    base.update(overrides)
    return base


class _FakeMatcher:
    def __init__(self, nearest, score):
        self._result = (nearest, score)

    def best_match(self, text):
        return self._result


def _stub_matcher(monkeypatch, nearest, score):
    monkeypatch.setattr(scope_semantic_check, "_get_matcher", lambda topics: _FakeMatcher(nearest, score))


def test_disabled_scope_semantic_check_does_not_change_existing_pipeline_behavior(monkeypatch):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(enabled=False)}
    )

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    assert result.blocked is False
    assert "scope_semantic_check" in [s.name for s in result.steps]


def test_deterministic_block_short_circuits_before_scope_semantic_check_runs(monkeypatch):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(enabled=True)}
    )

    def _unexpected(topics):
        raise AssertionError("scope_semantic_check must not run once a deterministic check already blocked")

    monkeypatch.setattr(scope_semantic_check, "_get_matcher", _unexpected)

    result = pipeline.run_input_guardrails("please delete all the files in the database")

    assert result.blocked is True
    assert "scope_semantic_check" not in [s.name for s in result.steps]


def test_scope_semantic_block_verdict_blocks_the_whole_pipeline(monkeypatch):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(enabled=True)}
    )
    _stub_matcher(monkeypatch, "how do I request time off", 0.05)

    result = pipeline.run_input_guardrails("what's the weather like today")

    assert result.blocked is True
    assert result.block_reason == "That's outside what I can help with here."


def test_scope_semantic_check_not_wired_into_output_pipeline():
    result = pipeline.run_output_guardrails("An entirely ordinary reply with nothing notable in it.")

    assert "scope_semantic_check" not in [s.name for s in result.steps]


def test_scope_semantic_runs_after_deberta_and_before_toxicity(monkeypatch):
    from app.services.guardrails import deberta_injection_check, toxicity_check

    monkeypatch.setattr(
        deberta_injection_check, "load_yaml_config", lambda name: {"deberta_injection_check": {"enabled": False}}
    )
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": {"enabled": False}})
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _cfg(enabled=True)}
    )
    _stub_matcher(monkeypatch, "how do I request time off", 0.90)

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    step_names = [s.name for s in result.steps]
    assert step_names.index("deberta_injection_check") < step_names.index("scope_semantic_check") < step_names.index(
        "toxicity_check"
    )
