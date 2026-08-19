"""services/guardrails/pii.py — placeholder vs. hash redaction modes
(docs/GUARDRAILS_ARCHITECTURE.md §11)."""

import pytest

from app.core.config import settings
from app.services.guardrails.pii import preview_redaction, redact_pii


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


def test_placeholder_mode_produces_opaque_tokens():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    text, step = redact_pii("email me at jane@example.com")
    assert text == "email me at [REDACTED_EMAIL]"
    assert step.action == "redact"


def test_mask_mode_is_the_default():
    # Confirmed policy: PII a user asks about always comes back masked, not
    # withheld outright or fully opaque — "mask" is config.py's default
    # guardrail_pii_mode, verified here WITHOUT explicitly setting it.
    settings.guardrail_redact_pii = True
    assert settings.guardrail_pii_mode == "mask"
    text, step = redact_pii("email me at jane@example.com")
    assert text == "email me at ja##.com"
    assert step.action == "redact"


def test_mask_mode_masks_email_local_part_and_hides_real_domain():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "mask"
    text, step = redact_pii("email me at jane@example.com")
    assert "jane@example.com" not in text
    assert "example.com" not in text  # the real domain is never shown in mask mode
    assert text == "email me at ja##.com"
    assert step.action == "redact"


def test_mask_mode_email_short_local_part_never_fully_exposed():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "mask"
    text, step = redact_pii("contact a@example.com now")
    assert "a@example.com" not in text
    assert "example.com" not in text
    assert text == "contact #.com now"
    assert step.action == "redact"


def test_mask_mode_masks_phone_first_two_and_last_one_digit():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "mask"
    text, step = redact_pii("call 312-555-0173")
    assert "3125550173" not in text
    assert text == "call 31#######3"
    assert step.action == "redact"


def test_mask_mode_phone_mask_scales_with_actual_digit_count():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "mask"
    # 7-digit NANP local format (no area code) — shorter number, shorter mask.
    text, step = redact_pii("call me at 555-0100")
    assert "5550100" not in text
    assert "55####0" in text
    assert step.action == "redact"


def test_reveal_last_on_email_reveals_local_part_not_the_domain_suffix():
    # Found live via the Policy Copilot ("show last 4 characters" on EMAIL):
    # a naive reveal_last applied to the raw string reveals the trailing
    # characters of the STRING, which for an email is the domain suffix
    # (".com"/".org"/...) — useless as a "which address" signal, and for a
    # non-.com domain a real regression since it shows the caller's REAL
    # domain where mask mode otherwise always hides it (see the domain-hiding
    # assertions above). reveal_last for EMAIL must reveal LOCAL-PART
    # characters and must still never show the real domain.
    result = preview_redaction("EMAIL", "jane.doe@example.com", action="MASK", reveal_last=4)
    assert result == "####.doe.com"
    assert "example" not in result


def test_reveal_last_on_email_never_leaks_a_non_com_domain():
    result = preview_redaction("EMAIL", "jane.doe@mycompany.org", action="MASK", reveal_last=4)
    assert result.endswith(".com")
    assert "mycompany" not in result
    assert "org" not in result


def test_reveal_last_on_email_still_hides_the_domain_when_reveal_exceeds_local_length():
    result = preview_redaction("EMAIL", "a@example.com", action="MASK", reveal_last=4)
    assert result == "#.com"
    assert "example" not in result


def test_reveal_last_on_phone_is_unaffected_by_the_email_fix():
    # PHONE keeps the pre-existing entity-agnostic reveal_last behavior —
    # this is a regression guard, not new behavior.
    result = preview_redaction("PHONE", "312-555-0173", action="MASK", reveal_last=4)
    assert result == "######0173"


def test_mask_mode_falls_back_to_placeholder_for_other_pii_types():
    # Only PHONE/EMAIL have a confirmed partial-mask format — every other
    # type keeps the fully opaque placeholder token even in "mask" mode.
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "mask"
    text, step = redact_pii("SSN 123-45-6789")
    assert "123-45-6789" not in text
    assert "[REDACTED_SSN]" in text
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
