"""services/guardrails/pipeline.py — wiring for gliner_check, the second
semantic PII check that runs on BOTH input and output, complementing
presidio_check's narrow structured-identifier allowlist. Mirrors
test_pipeline_presidio_wiring.py's conventions.
"""

from app.services.guardrails import gliner_check, pipeline


def _cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.6, "max_input_chars": 2000, "labels": []}
    base.update(overrides)
    return base


class _FakeModel:
    def __init__(self, entities):
        self._entities = entities

    def predict_entities(self, text, labels, threshold):
        return self._entities


def _stub_model(monkeypatch, entities):
    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _FakeModel(entities))


# --------------------------------------------------- input wiring

def test_disabled_gliner_check_does_not_change_existing_pipeline_behavior(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=False)})

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    assert result.blocked is False
    assert "gliner_check" in [s.name for s in result.steps]


def test_deterministic_block_short_circuits_before_gliner_check_runs(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})

    def _unexpected(model_name):
        raise AssertionError("gliner_check must not run once a deterministic check already blocked")

    monkeypatch.setattr(gliner_check, "_get_model", _unexpected)

    result = pipeline.run_input_guardrails("please delete all the files in the database")

    assert result.blocked is True
    assert "gliner_check" not in [s.name for s in result.steps]


def test_input_gliner_block_verdict_blocks_the_whole_pipeline(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [{"start": 0, "end": 16, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9}])

    result = pipeline.run_input_guardrails("I live at 42 Oakwood Lane, can you help me with something else?")

    assert result.blocked is True
    assert "personal information" in result.block_reason.lower()


def test_input_gliner_runs_after_presidio_check(monkeypatch):
    from app.services.guardrails import presidio_check

    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": {"enabled": False}})
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [])

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    step_names = [s.name for s in result.steps]
    assert step_names.index("presidio_check") < step_names.index("gliner_check")


# --------------------------------------------------- output wiring

def test_output_gliner_check_runs_after_presidio_check(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [])

    result = pipeline.run_output_guardrails("Full-time employees accrue 1.5 days of PTO per month.")

    step_names = [s.name for s in result.steps]
    assert step_names.index("presidio_check") < step_names.index("gliner_check")
    assert result.blocked is False


def test_output_gliner_block_verdict_blocks_the_reply(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(
        monkeypatch, [{"start": 0, "end": 16, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9}]
    )

    result = pipeline.run_output_guardrails("The employee's home address is 42 Oakwood Lane.")

    assert result.blocked is True
    assert "42 Oakwood Lane" not in result.block_reason


def test_output_gliner_pass_still_runs_deterministic_pii_redaction_after(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [])

    result = pipeline.run_output_guardrails("Contact jane@example.com for details.")

    assert result.blocked is False
    assert "jane@example.com" not in result.text
    assert "[REDACTED_EMAIL]" in result.text
    step_names = [s.name for s in result.steps]
    assert step_names == ["system_prompt_leak_check", "toxicity_check", "presidio_check", "gliner_check", "pii_redact"]


def test_output_gliner_infra_failure_fails_closed_by_default(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    result = pipeline.run_output_guardrails("An entirely ordinary, clean reply.")

    assert result.blocked is True
