"""services/guardrails/pipeline.py — wiring for toxicity_check, run on BOTH
input and output. Mirrors test_pipeline_gliner_wiring.py's conventions for a
dual-sided check.
"""

from app.services.guardrails import pipeline, toxicity_check


def _cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.7, "max_input_chars": 2000}
    base.update(overrides)
    return base


class _FakePipeline:
    def __init__(self, scores):
        self._scores = scores

    def __call__(self, text):
        return [self._scores]


def _stub_pipeline(monkeypatch, scores):
    monkeypatch.setattr(toxicity_check, "_get_pipeline", lambda model_name: _FakePipeline(scores))


_CLEAN = [{"label": "toxic", "score": 0.01}]
_TOXIC = [{"label": "toxic", "score": 0.95}]


def test_disabled_toxicity_check_does_not_change_existing_pipeline_behavior(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=False)})

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    assert result.blocked is False
    assert "toxicity_check" in [s.name for s in result.steps]


def test_deterministic_block_short_circuits_before_toxicity_check_runs(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=True)})

    def _unexpected(model_name):
        raise AssertionError("toxicity_check must not run once a deterministic check already blocked")

    monkeypatch.setattr(toxicity_check, "_get_pipeline", _unexpected)

    result = pipeline.run_input_guardrails("please delete all the files in the database")

    assert result.blocked is True
    assert "toxicity_check" not in [s.name for s in result.steps]


def test_toxicity_block_verdict_blocks_the_input_pipeline(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=True)})
    _stub_pipeline(monkeypatch, _TOXIC)

    result = pipeline.run_input_guardrails("some genuinely abusive message")

    assert result.blocked is True
    assert "abusive" in result.block_reason.lower()


def test_toxicity_block_verdict_blocks_the_output_pipeline(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=True)})
    _stub_pipeline(monkeypatch, _TOXIC)

    result = pipeline.run_output_guardrails("some genuinely abusive reply")

    assert result.blocked is True
    assert "harmful or abusive" in result.block_reason.lower()


def test_toxicity_check_runs_on_both_input_and_output(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=True)})
    _stub_pipeline(monkeypatch, _CLEAN)

    input_result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")
    output_result = pipeline.run_output_guardrails("Full-time employees accrue 1.5 days of PTO per month.")

    assert "toxicity_check" in [s.name for s in input_result.steps]
    assert "toxicity_check" in [s.name for s in output_result.steps]


def test_toxicity_runs_after_scope_semantic_and_before_presidio(monkeypatch):
    from app.services.guardrails import scope_semantic_check

    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": {"enabled": False}}
    )
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _cfg(enabled=True)})
    _stub_pipeline(monkeypatch, _CLEAN)

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    step_names = [s.name for s in result.steps]
    assert step_names.index("toxicity_check") < step_names.index("presidio_check")
