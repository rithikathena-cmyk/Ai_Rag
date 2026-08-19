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


def test_input_gliner_redacts_a_validated_candidate_and_does_not_block(monkeypatch):
    """Current contract (see gliner_check.py's docstring): a successful
    GLiNER detection redacts, it never blocks on its own — only the
    detector's own failure path still blocks."""
    from app.core.config import settings

    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [{"start": 10, "end": 25, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9}])
    original = settings.guardrail_pii_block_input
    settings.guardrail_pii_block_input = False
    try:
        result = pipeline.run_input_guardrails("I live at 42 Oakwood Lane, what's the weather like today")
    finally:
        settings.guardrail_pii_block_input = original

    assert "42 Oakwood Lane" not in result.text
    assert "[REDACTED_PHYSICAL_ADDRESS]" in result.text
    gliner_result_step = next(s for s in result.steps if s.name == "gliner_check")
    assert gliner_result_step.action == "redact"


def test_input_gliner_redact_blocks_when_block_input_policy_is_on(monkeypatch):
    """Same detection as above, but with guardrail_pii_block_input=True
    (this deployment's own regex pii_redact already respects this exact
    setting on input) — GLiNER's redact result must be gated by the same
    policy, not quietly softer than the check right next to it."""
    from app.core.config import settings

    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [{"start": 10, "end": 25, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9}])
    original = settings.guardrail_pii_block_input
    settings.guardrail_pii_block_input = True
    try:
        result = pipeline.run_input_guardrails("I live at 42 Oakwood Lane, what's the weather like today")
    finally:
        settings.guardrail_pii_block_input = original

    assert result.blocked is True
    assert result.blocking_step_name == "gliner_check"
    assert "personal information" in result.block_reason.lower()
    assert "42 Oakwood Lane" not in result.block_reason


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


def test_output_gliner_redacts_a_validated_candidate_into_the_reply(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(
        monkeypatch, [{"start": 32, "end": 47, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9}]
    )

    result = pipeline.run_output_guardrails("The employee's home address is 42 Oakwood Lane.")

    assert result.blocked is False
    assert "42 Oakwood Lane" not in result.text
    assert "[REDACTED_PHYSICAL_ADDRESS]" in result.text


def test_output_gliner_pass_still_runs_deterministic_pii_redaction_after(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})
    _stub_model(monkeypatch, [])

    result = pipeline.run_output_guardrails("Contact jane@example.com for details.")

    assert result.blocked is False
    assert "jane@example.com" not in result.text
    # EMAIL's safe default output action is REDACT — a full opaque
    # replacement, not a partial mask (see services/guardrail_policy/
    # pii_policy.py's _SAFE_PII_DEFAULTS and pii.py's _resolve_match()).
    assert "[REDACTED_EMAIL]" in result.text
    step_names = [s.name for s in result.steps]
    assert step_names == [
        "prompt_injection_check", "system_prompt_leak_check", "toxicity_check", "presidio_check",
        "gliner_check", "pii_redact",
    ]


def test_output_gliner_infra_failure_fails_closed_by_default(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    result = pipeline.run_output_guardrails("An entirely ordinary, clean reply.")

    assert result.blocked is True
