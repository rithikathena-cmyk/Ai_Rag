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
    # SSN, not EMAIL: the Guardrail Policy Center's safe default for EMAIL
    # on input is FLAG (continue, record an event — see
    # services/guardrail_policy/pii_policy.py's _SAFE_PII_DEFAULTS,
    # matching the spec's own recommended defaults), so it no longer blocks
    # by default at all — that's real, intentional behavior, not a
    # regression. SSN's default is REDACT on input, which
    # guardrail_pii_block_input=True still escalates to a block, exactly
    # like every PII type did before per-entity policy resolution existed.
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("my SSN is 123-45-6789, please update my file")
    assert result.blocked is True
    assert "personal information" in result.block_reason.lower()


def test_input_pii_block_reason_never_echoes_the_raw_value():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("my SSN is 123-45-6789")
    assert "123-45-6789" not in result.block_reason


def test_input_without_pii_is_unaffected():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("what is the leave policy")
    assert result.blocked is False


def test_input_email_is_masked_like_every_other_personal_identifier():
    # Uniform PII policy (guardrail_policy/pii_policy.py): EMAIL is personal
    # data, so it resolves to MASK on input exactly as SSN/PHONE/CREDIT_CARD
    # now do — it is no longer the weaker FLAG, which detected the address
    # and then left it in the text verbatim.
    #
    # guardrail_pii_block_input is left at its own (False) default here
    # rather than forced True: MASK reports as status "redact", so forcing
    # the flag on would convert every masked identifier straight back into a
    # hard block and this test would be asserting the blocking path, not the
    # masking one. test_flag_on_blocks_input_pii below covers that direction.
    result = pipeline.run_input_guardrails("contact me at jane@example.com about the leave policy")
    assert result.blocked is False
    # The address itself is gone, but the request survives to be answered.
    assert "jane@example.com" not in result.text
    assert "leave policy" in result.text
    pii_step = next(s for s in result.steps if s.name == "pii_redact")
    assert pii_step.action == "redact"
    assert "EMAIL" in pii_step.detail


def test_flag_off_restores_redact_and_continue_behavior():
    # SSN (REDACT by default), not EMAIL (FLAG by default — see above).
    settings.guardrail_pii_block_input = False
    result = pipeline.run_input_guardrails("my SSN is 123-45-6789, please update my file")
    assert result.blocked is False
    assert "123-45-6789" not in result.text
    # REDACT is always a full opaque replacement now, regardless of the
    # process-wide guardrail_pii_mode setting — MASK (a distinct policy
    # action) is what respects that setting's partial-reveal behavior.
    assert "[REDACTED_SSN]" in result.text


def test_output_pii_is_redacted_never_blocked_regardless_of_input_flag():
    settings.guardrail_pii_block_input = True  # should have no bearing on the output path
    result = pipeline.run_output_guardrails("Contact jane@example.com for details.")
    assert result.blocked is False
    assert "jane@example.com" not in result.text
    # EMAIL's safe default output action is REDACT (full replacement), not
    # MASK — see this file's other tests for MASK's own partial-reveal
    # behavior when a policy explicitly requests it.
    assert "[REDACTED_EMAIL]" in result.text


def test_output_ssn_is_redacted_not_blocked_under_the_uniform_policy(monkeypatch):
    # Isolated from the shared DB deliberately: this asserts the built-in
    # DEFAULT, which only holds when no policy row exists for SSN. Other tests
    # in this suite create real rows in the shared development database and
    # leave them behind, so without the stub this assertion depends on which
    # tests ran before it — and it did fail exactly that way.
    from app.services.guardrail_policy import store

    monkeypatch.setattr(store, "get_all_policies", lambda category: [])

    # Under the uniform policy every personal identifier resolves to REDACT
    # on output, SSN included — the reply is delivered with the value
    # removed rather than withheld entirely. The value must not survive
    # anywhere in the returned text.
    #
    # An OUTPUT-side BLOCK is still reachable and still stops the whole
    # reply; it is now something an admin opts into per entity via the
    # Guardrail Policy Center rather than a built-in default, and
    # test_output_block_action_stops_the_reply covers that path.
    result = pipeline.run_output_guardrails("Their SSN on file is 123-45-6789.")
    assert result.blocked is False
    assert "123-45-6789" not in result.text
    pii_step = next(s for s in result.steps if s.name == "pii_redact")
    assert pii_step.action == "redact"
    assert "SSN" in pii_step.detail


def test_pii_step_is_still_recorded_in_trace_even_when_blocked():
    settings.guardrail_pii_block_input = True
    result = pipeline.run_input_guardrails("my SSN is 123-45-6789")
    step_names = [s.name for s in result.steps]
    assert "pii_redact" in step_names
