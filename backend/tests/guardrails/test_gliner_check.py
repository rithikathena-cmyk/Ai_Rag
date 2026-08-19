"""services/guardrails/gliner_check.py — direct unit tests for
check_with_gliner()'s redaction/veto/overlap logic, independent of pipeline
wiring (see test_pipeline_gliner_wiring.py for the pipeline-level tests)."""

from app.services.guardrails import gliner_check


def _cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.6, "max_input_chars": 2000, "labels": []}
    base.update(overrides)
    return base


class _FakeModel:
    def __init__(self, entities):
        self._entities = entities

    def predict_entities(self, text, labels, threshold):
        return self._entities


def _stub(monkeypatch, entities, cfg_overrides=None):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(**(cfg_overrides or {}))})
    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _FakeModel(entities))


def test_disabled_returns_text_unchanged(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=False)})

    text, step = gliner_check.check_with_gliner("My address is 42 Oakwood Lane")

    assert text == "My address is 42 Oakwood Lane"
    assert step.action == "pass"


def test_no_entities_found_returns_text_unchanged(monkeypatch):
    _stub(monkeypatch, [])

    text, step = gliner_check.check_with_gliner("What is the annual leave accrual rate?")

    assert text == "What is the annual leave accrual rate?"
    assert step.action == "pass"


def test_single_validated_entity_is_redacted(monkeypatch):
    _stub(monkeypatch, [{"start": 14, "end": 29, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9}])

    text, step = gliner_check.check_with_gliner("My address is 42 Oakwood Lane")

    assert text == "My address is [REDACTED_PHYSICAL_ADDRESS]"
    assert step.action == "redact"
    assert "PHYSICAL_ADDRESS" in step.detail
    assert "42 Oakwood Lane" not in step.detail


def test_default_label_gets_a_short_canonical_token(monkeypatch):
    # A passport-shaped value, deliberately NOT a well-formed SSN — pii.py
    # has no deterministic passport recognizer, so gliner_validators.py's
    # _is_deterministic_ssn veto (see that module) does not apply here and
    # this label's own canonical-token behavior is what's under test. A real
    # SSN shape is covered separately by
    # test_gliner_validators.py's SSN-veto tests, since that specific case
    # is now intentionally vetoed in favor of pii.py's own SSN recognizer —
    # see PII-SSN-01/PII-SSN-04 in tests/security/pii/test_pii_entities.py.
    label = "government-issued identification number such as a social security number or passport number"
    _stub(monkeypatch, [{"start": 13, "end": 21, "text": "A1234567", "label": label, "score": 0.9}])

    text, step = gliner_check.check_with_gliner("My passport: A1234567")

    assert text == "My passport: [REDACTED_GOVERNMENT_ID]"
    assert step.action == "redact"


def test_vetoed_candidate_is_not_redacted(monkeypatch):
    """The regression case: an employee ID matching the configured pattern
    must not be redacted (or block anything) even though GLiNER scored it
    against the government-ID label."""
    from app.core.config import settings

    label = "government-issued identification number such as a social security number or passport number"
    _stub(monkeypatch, [{"start": 0, "end": 12, "text": "STF-MFG-41220", "label": label, "score": 0.77}])
    original = settings.guardrail_employee_id_pattern
    settings.guardrail_employee_id_pattern = r"[A-Z]{3}-[A-Z]{3}-\d{5}"
    try:
        text, step = gliner_check.check_with_gliner(
            "Who reported the incident, and what is their employee ID STF-MFG-41220?"
        )
    finally:
        settings.guardrail_employee_id_pattern = original

    assert "STF-MFG-41220" in text
    assert step.action == "pass"


def test_overlapping_candidates_keep_only_the_higher_confidence_one(monkeypatch):
    _stub(monkeypatch, [
        {"start": 0, "end": 15, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.6},
        {"start": 0, "end": 15, "text": "42 Oakwood Lane", "label": "financial account number", "score": 0.95},
    ])

    text, step = gliner_check.check_with_gliner("42 Oakwood Lane is where I live")

    assert text.count("[REDACTED_") == 1
    assert "[REDACTED_FINANCIAL_ACCOUNT]" in text


def test_multiple_non_overlapping_candidates_are_all_redacted(monkeypatch):
    _stub(monkeypatch, [
        {"start": 0, "end": 15, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.9},
        {"start": 30, "end": 45, "text": "987654321012", "label": "financial account number", "score": 0.9},
    ])

    text, step = gliner_check.check_with_gliner("42 Oakwood Lane, account number 987654321012")

    assert "42 Oakwood Lane" not in text
    assert "987654321012" not in text
    assert "[REDACTED_PHYSICAL_ADDRESS]" in text
    assert "[REDACTED_FINANCIAL_ACCOUNT]" in text


def test_detector_exception_fails_closed_by_default(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True)})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    text, step = gliner_check.check_with_gliner("An entirely ordinary message.")

    assert step.action == "block"


def test_detector_exception_fails_open_when_configured(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=True, fail_closed=False)})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    text, step = gliner_check.check_with_gliner("An entirely ordinary message.")

    assert step.action == "pass"
