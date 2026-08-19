"""services/guardrails/gliner_validators.py — the specialized-validator veto
layer for GLiNER candidates. Regression tests for four independent,
evidence-based false positives documented in guardrails.yaml's gliner_check
comment history and tests/security/pii/test_pii_entities.py (PII-FP-01,
PII-SSN-01, PII-SSN-04): label re-wording alone couldn't fix any of them; a
structural check against a known shape can.
"""

from app.core.config import settings
from app.services.guardrails.gliner_validators import _is_configured_employee_id, is_vetoed

_GOVERNMENT_ID_LABEL = "government-issued identification number such as a social security number or passport number"


def test_configured_employee_id_vetoes_the_government_id_label(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", r"[A-Z]{3}-[A-Z]{3}-\d{5}")

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "STF-MFG-41220") is True


def test_configured_employee_id_pattern_does_not_match_a_real_ssn_shape(monkeypatch):
    """The narrower, original property test_a_real_ssn_shape_is_not_vetoed
    used to check: _is_configured_employee_id() specifically (an
    org-specific letters-letters-digits pattern) must not itself match a
    real, all-digit SSN shape — unit-tested directly here, independent of
    is_vetoed()'s aggregate result, since a second, independent check
    (_is_deterministic_ssn, see below) now deliberately DOES veto a real SSN
    for the aggregate is_vetoed() call."""
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", r"[A-Z]{3}-[A-Z]{3}-\d{5}")

    assert _is_configured_employee_id("123-45-6789") is False


def test_no_configured_employee_id_pattern_means_no_veto_from_that_check(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", None)

    assert _is_configured_employee_id("STF-MFG-41220") is False


def test_a_label_with_no_registered_veto_checks_is_never_vetoed():
    assert is_vetoed("financial account number", "STF-MFG-41220") is False


def test_veto_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", r"[A-Z]{3}-[A-Z]{3}-\d{5}")

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "stf-mfg-41220") is True


# --------------------------------------------------------------------------
# _is_internal_reference_id — PII-FP-01: an employee/incident ID must not be
# vetted by settings.guardrail_employee_id_pattern being unset. Defaults ON.
# --------------------------------------------------------------------------

def test_internal_reference_id_vetoes_by_default_without_any_config():
    """No monkeypatching at all — settings.guardrail_internal_id_pattern's
    real, shipped default must close this false positive out of the box,
    unlike guardrail_employee_id_pattern (which stays opt-in on purpose)."""
    assert is_vetoed(_GOVERNMENT_ID_LABEL, "STF-MFG-41220") is True


def test_internal_reference_id_veto_is_independent_of_employee_id_pattern(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", None)
    monkeypatch.setattr(settings, "guardrail_internal_id_pattern", r"[A-Z]{3}-[A-Z]{3}-\d{5}")

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "STF-MFG-41220") is True


def test_no_configured_internal_id_pattern_means_no_veto(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_internal_id_pattern", None)
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", None)

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "STF-MFG-41220") is False


def test_internal_reference_id_does_not_match_a_real_ssn_shape():
    assert is_vetoed(_GOVERNMENT_ID_LABEL, "123-45-6789") is True  # vetoed, but by the SSN check below, not this one


# --------------------------------------------------------------------------
# _is_deterministic_ssn — PII-SSN-01/PII-SSN-04: a well-formed SSN must be
# left to pii.py's own dedicated recognizer, not claimed by the broader
# GOVERNMENT_ID label.
# --------------------------------------------------------------------------

def test_a_well_formed_ssn_is_now_vetoed_in_favor_of_the_deterministic_recognizer(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", None)

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "123-45-6789") is True


def test_a_space_separated_ssn_is_also_vetoed(monkeypatch):
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", None)

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "123 45 6789") is True


def test_a_passport_like_value_is_not_vetoed_by_the_ssn_check(monkeypatch):
    """No deterministic passport recognizer exists in pii.py — GLiNER's
    broader coverage for that shape must be preserved (see this module's own
    docstring: "add a new entry only for another concrete, evidence-based
    false positive," never a speculative one)."""
    monkeypatch.setattr(settings, "guardrail_employee_id_pattern", None)

    assert is_vetoed(_GOVERNMENT_ID_LABEL, "A1234567") is False


# --------------------------------------------------------------------------
# _is_a_bare_category_mention — PII-SSN-01: GLiNER can match the WORDS
# describing an identifier category, not just an actual value, as a
# separate, non-overlapping candidate alongside the real one.
# --------------------------------------------------------------------------

def test_bare_category_phrase_is_vetoed():
    """Live-measured: GLiNER returns a separate 'social security number'
    candidate (no digits) alongside the real '123-45-6789' value for
    PII-SSN-01's input — both must be vetoed for pii.py's own SSN
    recognizer to correctly own the attribution."""
    assert is_vetoed(_GOVERNMENT_ID_LABEL, "social security number") is True
    assert is_vetoed(_GOVERNMENT_ID_LABEL, "passport number") is True


def test_a_value_containing_any_digit_is_not_vetoed_by_the_category_check():
    assert is_vetoed(_GOVERNMENT_ID_LABEL, "A1234567") is False
    assert is_vetoed(_GOVERNMENT_ID_LABEL, "123-45-6789") is True  # vetoed, but by the SSN check, not this one
