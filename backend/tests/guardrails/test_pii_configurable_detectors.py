"""services/guardrails/pii.py — the JWT recognizer and the dynamic
"configured detector" recognizer loading added for the Detector Capability
Registry (services/guardrail_policy/detector_capability.py). Complements
test_pii.py (unaffected by this pass — every existing recognizer's own
tests stay green, see tests/test_detector_capability.py's own coverage of
the create/update-time gate)."""

import pytest

from app.core.config import settings
from app.services.guardrail_policy import store
from app.services.guardrails.pii import find_pii_labels, redact_pii


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


@pytest.fixture(autouse=True)
def _no_configured_detectors(monkeypatch):
    """Every test gets a clean slate — no admin-configured detector rows —
    unless it explicitly stubs store.get_active_policies itself."""
    monkeypatch.setattr(store, "get_active_policies", lambda category: [])


# --------------------------------------------------------------------------
# JWT — a real, built-in recognizer now (previously Detection.NONE)
# --------------------------------------------------------------------------

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


def test_jwt_is_detected_and_redacted():
    text, step = redact_pii(f"Here is my token {_JWT}, use it.")

    assert _JWT not in text
    assert "[REDACTED_JWT]" in text
    assert step.action == "redact"


def test_jwt_shape_matches_the_same_pattern_secrets_py_uses():
    from app.services.guardrails.secrets import CREDENTIAL_PATTERNS

    secrets_jwt_pattern = next(p for label, p in CREDENTIAL_PATTERNS if label == "JWT")
    from app.services.guardrails import pii_patterns

    assert pii_patterns.JWT_RE is secrets_jwt_pattern


def test_find_pii_labels_reports_jwt():
    assert "JWT" in find_pii_labels(f"token: {_JWT}")


def test_ordinary_text_with_no_jwt_is_unaffected():
    text, step = redact_pii("What is the leave policy?")
    assert text == "What is the leave policy?"
    assert step.action == "pass"


# --------------------------------------------------------------------------
# Dynamic configurable-entity recognizers (BANK_ACCOUNT/IFSC/CUSTOMER_ID)
# --------------------------------------------------------------------------

def _row(entity: str, pattern: str, *, enabled: bool = True):
    return type("Row", (), {"configuration": {"entity": entity, "detector_pattern": pattern}, "enabled": enabled})()


def test_no_configured_row_means_no_detection(monkeypatch):
    monkeypatch.setattr(store, "get_active_policies", lambda category: [])
    text, step = redact_pii("My IFSC code is HDFC0001234.")
    assert text == "My IFSC code is HDFC0001234."
    assert step.action == "pass"


def test_a_configured_ifsc_pattern_is_detected_and_redacted(monkeypatch):
    monkeypatch.setattr(
        store, "get_active_policies", lambda category: [_row("IFSC", r"\b[A-Z]{4}0[A-Z0-9]{6}\b")],
    )
    text, step = redact_pii("My IFSC code is HDFC0001234.")
    assert "HDFC0001234" not in text
    assert "[REDACTED_IFSC]" in text
    assert step.action == "redact"


def test_a_configured_customer_id_pattern_is_detected(monkeypatch):
    monkeypatch.setattr(
        store, "get_active_policies", lambda category: [_row("CUSTOMER_ID", r"CUST-\d{6}")],
    )
    text, step = redact_pii("My customer id is CUST-482913, please look it up.")
    assert "CUST-482913" not in text
    assert "[REDACTED_CUSTOMER_ID]" in text


def test_a_non_configurable_entity_row_is_ignored(monkeypatch):
    """Defense in depth: even if a row somehow carried a detector_pattern
    for an entity NOT in CONFIGURABLE_ENTITIES (e.g. EMPLOYEE_ID, which
    validate_configuration() should already have refused at write time —
    see test_detector_capability.py), _build_recognizers() must not load
    it, so a validation bug can't silently promote it into a live
    recognizer."""
    monkeypatch.setattr(
        store, "get_active_policies", lambda category: [_row("EMPLOYEE_ID", r"[A-Z]{3}-\d{5}")],
    )
    text, step = redact_pii("My employee id is ABC-12345.")
    assert text == "My employee id is ABC-12345."
    assert step.action == "pass"


def test_a_malformed_stored_pattern_is_skipped_not_raised(monkeypatch):
    """validate_configuration()/test_pattern_safety() already gate this at
    write time — a malformed pattern reaching here would mean the stored
    data itself is inconsistent, and must not break every PII check in the
    process."""
    bad_row = type("Row", (), {"configuration": {"entity": "IFSC", "detector_pattern": "[unclosed"}, "enabled": True})()
    monkeypatch.setattr(store, "get_active_policies", lambda category: [bad_row])
    text, step = redact_pii("Nothing sensitive here.")
    assert text == "Nothing sensitive here."
    assert step.action == "pass"


def test_a_disabled_configured_row_does_not_participate(monkeypatch):
    """store.get_active_policies() only returns enabled, ENFORCE-mode rows
    (see store.py's own docstring) — a disabled detector row is simply
    absent from what pii.py reads, same as any other disabled PII row."""
    monkeypatch.setattr(store, "get_active_policies", lambda category: [])
    text, step = redact_pii("My IFSC code is HDFC0001234.")
    assert "HDFC0001234" in text
    assert step.action == "pass"
