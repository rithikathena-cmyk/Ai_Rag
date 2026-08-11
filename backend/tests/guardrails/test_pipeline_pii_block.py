"""services/guardrails/pipeline.py — input PII now blocks by default
(docs/GUARDRAILS_ARCHITECTURE.md §11), output PII never blocks (the model
already generated it — redaction is what's left to do)."""

import pytest

from app.core.config import settings
from app.services.guardrails import pipeline


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_pii_block_input, settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    yield
    settings.guardrail_pii_block_input, settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


def test_input_pii_blocks_by_default():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("contact me at jane@example.com about the leave policy")
    assert result.blocked is True
    assert "personal information" in result.block_reason.lower()


def test_input_pii_block_reason_never_echoes_the_raw_value():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("contact me at jane@example.com")
    assert "jane@example.com" not in result.block_reason


def test_input_without_pii_is_unaffected():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("what is the leave policy")
    assert result.blocked is False


def test_flag_off_restores_redact_and_continue_behavior():
    settings.guardrail_pii_block_input = False
    result = pipeline.run_input_guardrails("contact me at jane@example.com about the leave policy")
    assert result.blocked is False
    assert "jane@example.com" not in result.text
    assert "[REDACTED_EMAIL]" in result.text


def test_output_pii_is_redacted_never_blocked_regardless_of_input_flag():
    settings.guardrail_pii_block_input = True  # should have no bearing on the output path
    result = pipeline.run_output_guardrails("Contact jane@example.com for details.")
    assert result.blocked is False
    assert "jane@example.com" not in result.text
    assert "[REDACTED_EMAIL]" in result.text


def test_pii_step_is_still_recorded_in_trace_even_when_blocked():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("contact me at jane@example.com")
    step_names = [s.name for s in result.steps]
    assert "pii_redact" in step_names
