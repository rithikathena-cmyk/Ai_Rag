"""services/guardrails/pii.py — per-type detection accuracy. Exercises
redact_pii() end-to-end (matching this suite's existing test_pii.py
convention) rather than the raw regexes in pii_patterns.py directly, since
end-to-end redaction behavior is what actually matters — a regex that
matches in isolation but gets rejected by a validator, or vice versa,
should be caught here either way.
"""

import pytest

from app.core.config import settings
from app.services.guardrails.pii import redact_pii


@pytest.fixture(autouse=True)
def _placeholder_mode():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


# --------------------------------------------------------------- email ---

_POSITIVE_EMAILS = [
    "user@example.com",
    "user.name@example.com",
    "user+tag@example.com",
    "first.last@mail.com",
    "user@company.co.uk",
    "user@company.co.in",
    "user@subdomain.company.com",
    "USER@EXAMPLE.COM",
    "user@example.org",
    "user@example.net",
    "user@example.edu",
    "user@example.in",
]


@pytest.mark.parametrize("email", _POSITIVE_EMAILS)
def test_email_positive(email):
    text, step = redact_pii(f"contact me at {email} please")
    assert email not in text
    assert "[REDACTED_EMAIL]" in text
    assert step.action == "redact"


_NEGATIVE_EMAILS = [
    "not-an-email",
    "foo@",
    "@example.com",
    "example.com",
    "user@",
    "user@.",
]


@pytest.mark.parametrize("text", _NEGATIVE_EMAILS)
def test_email_negative(text):
    result, step = redact_pii(f"see: {text} end")
    assert "[REDACTED_EMAIL]" not in result
    assert "EMAIL" not in step.detail


def test_email_hash_mode_is_case_insensitive():
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    text1, _ = redact_pii("email John.Doe@MAIL.COM")
    text2, _ = redact_pii("email john.doe@mail.com")
    token1 = text1.split("[REDACTED_EMAIL_")[1].split("]")[0]
    token2 = text2.split("[REDACTED_EMAIL_")[1].split("]")[0]
    assert token1 == token2


# --------------------------------------------------------------- phone ---

_POSITIVE_PHONES = [
    "9876543210",
    "+91 9876543210",
    "+91-9876543210",
    "+91 (98765) 43210",
    "091-9876543210",
    "+1 415 555 2671",
    "+44 20 7946 0958",
    "98765-43210",
    "98765 43210",
]


@pytest.mark.parametrize("phone", _POSITIVE_PHONES)
def test_phone_positive(phone):
    text, step = redact_pii(f"call me at {phone} tomorrow")
    assert "[REDACTED_PHONE]" in text
    assert step.action == "redact"


_NEGATIVE_PHONES = ["123", "12345", "2026", "999", "100000"]


@pytest.mark.parametrize("text", _NEGATIVE_PHONES)
def test_phone_negative(text):
    result, step = redact_pii(f"the value is {text} exactly")
    assert "[REDACTED_PHONE]" not in result
    assert result == f"the value is {text} exactly"


def test_phone_hash_unifies_indian_formats():
    """+91 9876543210, +919876543210, and 09876543210 are the same
    underlying number and must hash identically — see
    pii_validators.canonicalize_phone()'s docstring."""
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    variants = ["+91 9876543210", "+919876543210", "09876543210", "9876543210"]
    tokens = set()
    for v in variants:
        text, _ = redact_pii(f"call {v} now")
        tokens.add(text.split("[REDACTED_PHONE_")[1].split("]")[0])
    assert len(tokens) == 1, f"expected one shared hash, got {tokens}"


# ----------------------------------------------------------------- pan ---

_POSITIVE_PAN = ["ABCDE1234F", "abcde1234f", "AbCdE1234f"]


@pytest.mark.parametrize("pan", _POSITIVE_PAN)
def test_pan_positive(pan):
    text, step = redact_pii(f"PAN number is {pan} on file")
    assert pan not in text
    assert "[REDACTED_PAN]" in text
    assert step.action == "redact"


_NEGATIVE_PAN = [
    "ABCDEFGHIJ",  # 10 letters, no digits
    "1234567890",  # 10 digits, no letters
    "ABCD1234FG",  # wrong letter/digit split
    "ABCDE12345",  # 5 digits instead of 4, no trailing letter
]


@pytest.mark.parametrize("text", _NEGATIVE_PAN)
def test_pan_negative(text):
    result, step = redact_pii(f"code: {text} stored")
    assert "[REDACTED_PAN]" not in result


# ------------------------------------------------------------- aadhaar ---


def test_aadhaar_spaced_format_positive():
    text, step = redact_pii("Aadhaar: 1234 5678 9012 on record")
    assert "1234 5678 9012" not in text
    assert "[REDACTED_AADHAAR]" in text
    assert step.action == "redact"


def test_aadhaar_bare_format_positive():
    text, step = redact_pii("Aadhaar: 123456789012 on record")
    assert "123456789012" not in text
    assert "[REDACTED_AADHAAR]" in text


def test_aadhaar_verhoeff_valid_number_redacts():
    # Generated with a real Verhoeff check digit — see pii_validators.py.
    text, step = redact_pii("Aadhaar 234123412346 confirmed")
    assert "[REDACTED_AADHAAR]" in text


def test_aadhaar_starting_with_0_or_1_still_redacts():
    """UIDAI never issues a real Aadhaar starting with 0/1, but the
    redaction gate (is_aadhaar_shaped) deliberately doesn't enforce that —
    see its docstring for why recall wins over precision here. The stricter
    rule is still enforced by is_valid_aadhaar(), just not used to decide
    whether to redact."""
    result, _ = redact_pii("reference 012345678901 assigned")
    assert "012345678901" not in result


# -------------------------------------------------- phone confidence tier ---

_PHONE_HIGH_CONFIDENCE = [
    "Phone: 9876543210",
    "Mobile: 98765 43210",
    "Contact number: +91 98765 43210",
    "Call me at +1-202-555-0198",
    "Telephone: +44 20 7946 0958",
    "Phone number is 9876543210",
    "+91 98765 43210",
    "+919876543210",
]


@pytest.mark.parametrize("text", _PHONE_HIGH_CONFIDENCE)
def test_phone_high_or_medium_confidence_redacts(text):
    _redacted, step = redact_pii(text)
    assert step.action == "redact"
    assert "PHONE" in step.detail


_PHONE_MEDIUM_CONFIDENCE_FORMATTED = [
    "(202) 555-0198",
    "202-555-0198",
    "Mobile: 98765-43210",
]


@pytest.mark.parametrize("text", _PHONE_MEDIUM_CONFIDENCE_FORMATTED)
def test_phone_formatted_without_context_still_redacts(text):
    """No explicit context word needed when the value itself is
    internally formatted (MEDIUM confidence) — only a bare, unformatted
    digit run needs context to redact."""
    _redacted, step = redact_pii(text)
    assert step.action == "redact"


_PHONE_LOW_CONFIDENCE_NOT_REDACTED = [
    "1234567890",
    "1000000000",
    "2026010112",
    "The order number is 1234567890.",
    "EMP-1234567890",
    "PO-1234567890",
    "ORDER-1234567890",
]


@pytest.mark.parametrize("text", _PHONE_LOW_CONFIDENCE_NOT_REDACTED)
def test_bare_digit_run_without_phone_context_is_not_redacted(text):
    """The known false positive this hardening pass fixes: a bare 10-digit
    number with no phone-context word, no formatting, and no country code
    is exactly as likely to be an id/quantity/reference number as a phone
    number — LOW confidence, not auto-redacted."""
    redacted, step = redact_pii(text)
    assert redacted == text
    assert step.action == "pass"


def test_phone_confidence_does_not_regress_existing_recall():
    """Every phone format from the original PII hardening pass still
    redacts when wrapped in ordinary contextual phrasing (all of this
    suite's existing phone tests already use "call me at ..." framing,
    which itself is HIGH-confidence context — this test makes that
    dependency explicit rather than incidental)."""
    for phone in ("9876543210", "+91 9876543210", "98765-43210", "98765 43210"):
        _redacted, step = redact_pii(f"call me at {phone} tomorrow")
        assert step.action == "redact", phone


# ---------------------------------------------------------------- IP address ---

_IP_POSITIVE = ["192.168.1.1", "10.0.0.1", "255.255.255.255", "8.8.8.8"]


@pytest.mark.parametrize("ip", _IP_POSITIVE)
def test_ip_address_positive(ip):
    text, step = redact_pii(f"My IP is {ip} on the VPN.")
    assert ip not in text
    assert "[REDACTED_IP_ADDRESS]" in text
    assert step.action == "redact"


_IP_NEGATIVE = [
    "version 1.002.003.4",  # leading zero on a multi-digit octet — not canonical IPv4
    "999.999.999.999",  # each octet out of range
    "1.2.3.4.5",  # too many groups
    "not.an.ip.address",
]


@pytest.mark.parametrize("text", _IP_NEGATIVE)
def test_ip_address_negative(text):
    result, _step = redact_pii(text)
    assert "[REDACTED_IP_ADDRESS]" not in result


# ------------------------------------------------------------ date of birth ---


def test_date_of_birth_with_context_redacts():
    for phrase in (
        "Date of birth: 03/14/1990",
        "DOB: 1990-03-14",
        "She was born on March 14, 1990.",
        "Birthday: 03-14-1990",
    ):
        text, step = redact_pii(phrase)
        assert step.action == "redact", phrase
        assert "[REDACTED_DATE_OF_BIRTH]" in text


def test_bare_date_without_dob_context_is_not_redacted():
    """A date is one of the most common things in ordinary business text
    (deadlines, report dates, meeting times) — redacting every date-shaped
    string as DOB would be a severe false-positive generator, so context is
    a hard requirement here, not a confidence tier."""
    for phrase in (
        "The report is due 03/14/1990.",
        "Meeting scheduled for 2024-03-14.",
        "The policy was updated on March 14, 1990.",
    ):
        result, step = redact_pii(phrase)
        assert "[REDACTED_DATE_OF_BIRTH]" not in result
        assert step.action == "pass"


# --------------------------------------- HMAC hashing covers the new types ---


def test_hash_mode_covers_ip_address_and_date_of_birth():
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    text, step = redact_pii("IP 192.168.1.1, DOB: 1990-03-14")
    assert "192.168.1.1" not in text
    assert "1990-03-14" not in text
    assert "[REDACTED_IP_ADDRESS_" in text
    assert "[REDACTED_DATE_OF_BIRTH_" in text


# -------------------------- literal regression list, precision hardening ---
# The exact positive/negative examples from the precision-hardening pass
# this suite was built against, kept as one dedicated block for direct
# traceability back to that spec rather than folded into the generic lists
# above (several of which already cover the same shapes with different
# literal values).

_REGRESSION_EMAILS = [
    "john.doe@example.com",
    "john.doe@example.co.uk",
    "john_doe@example.org",
    "john+finance@example.com",
]


@pytest.mark.parametrize("email", _REGRESSION_EMAILS)
def test_regression_email_examples(email):
    text, step = redact_pii(f"Reach {email} for details.")
    assert email not in text
    assert step.action == "redact"


_REGRESSION_PHONE_POSITIVE = [
    "+91 98765 43210",
    "+919876543210",
    "+1-202-555-0198",
    "(202) 555-0198",
    "Phone: 9876543210",
    "Mobile: 98765-43210",
]


@pytest.mark.parametrize("text", _REGRESSION_PHONE_POSITIVE)
def test_regression_phone_examples_redact(text):
    assert redact_pii(text)[1].action == "redact"


_REGRESSION_PHONE_NOT_HIGH_CONFIDENCE = ["1234567890", "EMP-1234567890", "ORDER-1234567890"]


@pytest.mark.parametrize("text", _REGRESSION_PHONE_NOT_HIGH_CONFIDENCE)
def test_regression_ambiguous_identifiers_are_not_redacted(text):
    redacted, step = redact_pii(text)
    assert redacted == text
    assert step.action == "pass"
