"""services/guardrails/pii_validators.py — normalization + structural/
checksum validation, tested directly (not through redact_pii()) since these
are pure functions with no config/settings dependency."""

from app.services.guardrails import pii_validators as v


# --------------------------------------------------------------- email ---

def test_normalize_email_lowercases_and_strips():
    assert v.normalize_email("  John.Doe@MAIL.COM  ") == "john.doe@mail.com"


# --------------------------------------------------------------- phone ---

def test_is_valid_phone_accepts_indian_mobile():
    assert v.is_valid_phone("9876543210") is True
    assert v.is_valid_phone("+91 9876543210") is True
    assert v.is_valid_phone("09876543210") is True


def test_is_valid_phone_accepts_international():
    assert v.is_valid_phone("+1 415 555 2671") is True
    assert v.is_valid_phone("+44 20 7946 0958") is True


def test_is_valid_phone_rejects_short_numbers():
    for bad in ("123", "12345", "2026", "999", "100000"):
        assert v.is_valid_phone(bad) is False, bad


def test_canonicalize_phone_unifies_indian_formats():
    canon = v.canonicalize_phone("9876543210")
    assert v.canonicalize_phone("+91 9876543210") == canon
    assert v.canonicalize_phone("+919876543210") == canon
    assert v.canonicalize_phone("09876543210") == canon


def test_canonicalize_phone_differs_for_different_numbers():
    assert v.canonicalize_phone("9876543210") != v.canonicalize_phone("9876543211")


# ----------------------------------------------------------------- pan ---

def test_is_valid_pan_accepts_correct_structure_any_case():
    assert v.is_valid_pan("ABCDE1234F") is True
    assert v.is_valid_pan("abcde1234f") is True


def test_is_valid_pan_rejects_wrong_structure():
    assert v.is_valid_pan("ABCDEFGHIJ") is False
    assert v.is_valid_pan("1234567890") is False
    assert v.is_valid_pan("ABCDE12345") is False
    assert v.is_valid_pan("ABCD1234FG") is False


def test_normalize_pan_uppercases():
    assert v.normalize_pan("abcde1234f") == "ABCDE1234F"


# ------------------------------------------------------------- aadhaar ---

def test_is_aadhaar_shaped_accepts_any_12_digit_run():
    """Deliberately does NOT exclude a leading 0/1 (that's
    is_valid_aadhaar()'s job) — redaction favors recall over precision
    here, see is_aadhaar_shaped()'s docstring."""
    assert v.is_aadhaar_shaped("234123412346") is True
    assert v.is_aadhaar_shaped("1234 5678 9012") is True
    assert v.is_aadhaar_shaped("123456789012") is True


def test_is_aadhaar_shaped_rejects_wrong_length():
    assert v.is_aadhaar_shaped("12345678901") is False  # 11 digits
    assert v.is_aadhaar_shaped("1234567890123") is False  # 13 digits


def test_is_valid_aadhaar_verhoeff_checksum():
    # Generated with a real Verhoeff check digit (11-digit prefix
    # "23412341234" -> check digit 6). See pii_validators.py's docstring
    # for why redaction doesn't gate on this (is_aadhaar_shaped does) while
    # this function is still exposed for callers that need real validity.
    assert v.is_valid_aadhaar("234123412346") is True
    # Mutate the check digit only -> must now fail.
    assert v.is_valid_aadhaar("234123412347") is False


def test_is_valid_aadhaar_rejects_placeholder_examples():
    """The spec's own illustrative "1234 5678 9012" is not a real
    checksummed Aadhaar number — confirms is_valid_aadhaar() is a strict
    validity check, distinct from is_aadhaar_shaped()'s redaction gate."""
    assert v.is_valid_aadhaar("1234 5678 9012") is False
    assert v.is_valid_aadhaar("123456789012") is False


def test_is_valid_aadhaar_rejects_leading_0_or_1():
    """UIDAI never issues a number starting with 0 or 1 — even with a
    would-be-correct Verhoeff digit, is_valid_aadhaar() must still reject
    these (is_aadhaar_shaped(), used for the redaction gate itself,
    deliberately does not apply this rule — see its docstring)."""
    assert v.is_valid_aadhaar("012345678901") is False
    assert v.is_valid_aadhaar("123456789012") is False
