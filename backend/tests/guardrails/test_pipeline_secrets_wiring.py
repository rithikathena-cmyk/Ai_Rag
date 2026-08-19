"""services/guardrails/pipeline.py — wiring for secret_detected_check, run
right after length_check and before prompt_injection_check in
run_input_guardrails(). Mirrors test_pipeline_deberta_wiring.py's
conventions.
"""

from app.services.guardrails import injection, pipeline, secrets


def test_disabled_secret_check_does_not_change_existing_pipeline_behavior(monkeypatch):
    monkeypatch.setattr(secrets, "load_yaml_config", lambda name: {"secret_detection": {"enabled": False}})

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    assert result.blocked is False
    assert "secret_detected_check" in [s.name for s in result.steps]


def test_length_block_short_circuits_before_secret_check_runs(monkeypatch):
    monkeypatch.setattr(secrets, "load_yaml_config", lambda name: {"secret_detection": {"enabled": True}})

    result = pipeline.run_input_guardrails("")

    assert result.blocked is True
    assert result.blocking_step_name == "length_check"
    assert "secret_detected_check" not in [s.name for s in result.steps]


def test_secret_block_verdict_blocks_the_whole_pipeline(monkeypatch):
    monkeypatch.setattr(secrets, "load_yaml_config", lambda name: {"secret_detection": {"enabled": True}})

    result = pipeline.run_input_guardrails("Here is my AWS key: AKIAABCDEFGHIJKLMNOP, can you use it?")

    assert result.blocked is True
    assert result.blocking_step_name == "secret_detected_check"
    assert "credential" in result.block_reason.lower()
    assert "AKIA" not in result.block_reason


def test_secret_check_runs_before_prompt_injection_check(monkeypatch):
    monkeypatch.setattr(secrets, "load_yaml_config", lambda name: {"secret_detection": {"enabled": True}})

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    step_names = [s.name for s in result.steps]
    assert step_names.index("length_check") < step_names.index("secret_detected_check") < step_names.index(
        "prompt_injection_check"
    )


def test_secret_not_wired_into_output_pipeline_directly():
    # Output PII/secret leak protection lives in system_prompt_leak_check
    # (services/guardrails/output.py), which reuses secrets.CREDENTIAL_PATTERNS
    # — this asserts run_output_guardrails() doesn't ALSO run a second,
    # separate secret_detected_check step (that coverage is already provided
    # by system_prompt_leak_check, not duplicated).
    result = pipeline.run_output_guardrails("An entirely ordinary reply with nothing notable in it.")

    assert "secret_detected_check" not in [s.name for s in result.steps]
