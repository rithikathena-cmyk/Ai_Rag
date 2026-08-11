"""services/guardrails/pii.py — placeholder vs. hash redaction modes
(docs/GUARDRAILS_ARCHITECTURE.md §11)."""

import pytest

from app.core.config import settings
from app.services.guardrails.pii import redact_pii


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode, settings.guardrail_pii_hash_salt)
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode, settings.guardrail_pii_hash_salt = original


def test_disabled_check_passes_through_unchanged():
    settings.guardrail_redact_pii = False
    text, step = redact_pii("email me at jane@example.com")
    assert text == "email me at jane@example.com"
    assert step.action == "pass"


def test_no_pii_passes_through():
    settings.guardrail_redact_pii = True
    text, step = redact_pii("what is the leave policy")
    assert text == "what is the leave policy"
    assert step.action == "pass"


def test_placeholder_mode_is_the_default():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    text, step = redact_pii("email me at jane@example.com")
    assert text == "email me at [REDACTED_EMAIL]"
    assert step.action == "redact"


def test_hash_mode_produces_a_labeled_hash_token():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    text, step = redact_pii("email me at jane@example.com")
    assert "jane@example.com" not in text
    assert "[REDACTED_EMAIL_" in text
    assert text.count("[REDACTED_EMAIL_") == 1
    # 8 hex chars between the label and the closing bracket
    token = text.split("[REDACTED_EMAIL_")[1].split("]")[0]
    assert len(token) == 8
    all(c in "0123456789abcdef" for c in token)


def test_hash_mode_is_consistent_for_the_same_value():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    text1, _ = redact_pii("contact jane@example.com")
    text2, _ = redact_pii("please reach jane@example.com today")
    token1 = text1.split("[REDACTED_EMAIL_")[1].split("]")[0]
    token2 = text2.split("[REDACTED_EMAIL_")[1].split("]")[0]
    assert token1 == token2


def test_hash_mode_differs_for_different_values():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    text1, _ = redact_pii("contact jane@example.com")
    text2, _ = redact_pii("contact john@example.com")
    token1 = text1.split("[REDACTED_EMAIL_")[1].split("]")[0]
    token2 = text2.split("[REDACTED_EMAIL_")[1].split("]")[0]
    assert token1 != token2


def test_hash_mode_differs_with_different_salt():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "hash"

    settings.guardrail_pii_hash_salt = "salt-one"
    text1, _ = redact_pii("contact jane@example.com")
    settings.guardrail_pii_hash_salt = "salt-two"
    text2, _ = redact_pii("contact jane@example.com")

    token1 = text1.split("[REDACTED_EMAIL_")[1].split("]")[0]
    token2 = text2.split("[REDACTED_EMAIL_")[1].split("]")[0]
    assert token1 != token2


def test_hash_mode_covers_all_pii_types():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    text, step = redact_pii("SSN 123-45-6789, call 555-123-4567")
    assert "123-45-6789" not in text
    assert "[REDACTED_SSN_" in text
    assert "[REDACTED_PHONE_" in text


def test_unknown_mode_falls_back_to_placeholder():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "not-a-real-mode"
    text, step = redact_pii("email me at jane@example.com")
    assert text == "email me at [REDACTED_EMAIL]"
