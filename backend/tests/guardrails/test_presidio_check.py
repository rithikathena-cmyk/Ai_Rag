"""services/guardrails/presidio_check.py — the Presidio-based advanced
input check that replaced the former LLM-judge llm_advanced_check. Mocks
presidio_check._get_analyzer() directly (rather than requiring the real
spaCy model to load per test) so this suite stays fast and deterministic,
matching this package's established convention of stubbing the I/O/model
boundary — see e.g. test_pii_patterns.py, the old (deleted) test_llm_check.py.
"""

import pytest
from presidio_analyzer import RecognizerResult

from app.services.guardrails import presidio_check


def _cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.7, "max_input_chars": 2000, "entities": []}
    base.update(overrides)
    return base


class _FakeAnalyzer:
    def __init__(self, results):
        self._results = results
        self.calls = []

    def analyze(self, *, text, language, entities):
        self.calls.append({"text": text, "language": language, "entities": entities})
        return self._results


@pytest.fixture(autouse=True)
def _reset_analyzer_cache():
    # presidio_check._analyzer is a lazily-built module-level singleton —
    # reset it around every test so one test's monkeypatched fake never
    # leaks into the next.
    presidio_check._analyzer = None
    yield
    presidio_check._analyzer = None


def _stub_analyzer(monkeypatch, results):
    fake = _FakeAnalyzer(results)
    monkeypatch.setattr(presidio_check, "_get_analyzer", lambda: fake)
    return fake


def test_disabled_is_a_no_op_and_never_builds_the_analyzer(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg(enabled=False)})

    def _unexpected():
        raise AssertionError("_get_analyzer must not be called when the check is disabled")

    monkeypatch.setattr(presidio_check, "_get_analyzer", _unexpected)

    step = presidio_check.check_with_presidio("hello")

    assert step.action == "pass"
    assert "disabled" in step.detail.lower()


def test_empty_input_short_circuits_without_calling_analyzer(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})

    def _unexpected():
        raise AssertionError("_get_analyzer must not be called for empty input")

    monkeypatch.setattr(presidio_check, "_get_analyzer", _unexpected)

    step = presidio_check.check_with_presidio("   ")

    assert step.action == "pass"


def test_pass_when_no_entities_detected(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})
    _stub_analyzer(monkeypatch, [])

    step = presidio_check.check_with_presidio("What is the annual leave accrual rate?")

    assert step.action == "pass"
    assert step.name == "presidio_check"


def test_block_on_high_confidence_passport_number(monkeypatch):
    # Uses US_PASSPORT (in _ALLOWED_ENTITIES), not EMAIL_ADDRESS/US_SSN —
    # those are deliberately excluded from this check's allowlist since
    # pii.py already owns them and guardrail_pii_block_input governs their
    # blocking policy; see presidio_check.py's module docstring.
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})
    _stub_analyzer(
        monkeypatch, [RecognizerResult(entity_type="US_PASSPORT", start=15, end=24, score=0.9)]
    )

    step = presidio_check.check_with_presidio("My passport number is 123456789")

    assert step.action == "block"
    assert "US_PASSPORT" in step.detail


def test_below_threshold_entity_does_not_block(monkeypatch):
    # Calibrated case: Presidio's default PHONE_NUMBER recognizer scores a
    # real phone number ~0.4 in practice, below the default 0.7 threshold —
    # this must pass, not block, at the default config.
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})
    _stub_analyzer(
        monkeypatch, [RecognizerResult(entity_type="PHONE_NUMBER", start=11, end=23, score=0.4)]
    )

    step = presidio_check.check_with_presidio("Call me at 206-555-0164")

    assert step.action == "pass"


def test_mixed_allowed_and_unrequested_entity_types_blocks_only_on_the_allowed_one(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})
    _stub_analyzer(
        monkeypatch,
        [
            RecognizerResult(entity_type="US_BANK_NUMBER", start=10, end=21, score=0.85),
            RecognizerResult(entity_type="ORGANIZATION", start=0, end=3, score=0.85),
        ],
    )

    step = presidio_check.check_with_presidio("My IBAN is GB29NWBK60161331926819")

    assert step.action == "block"
    assert "US_BANK_NUMBER" in step.detail


def test_analyzer_error_fails_closed_by_default(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})

    class _Boom:
        def analyze(self, **kwargs):
            raise RuntimeError("model not loaded")

    monkeypatch.setattr(presidio_check, "_get_analyzer", lambda: _Boom())

    step = presidio_check.check_with_presidio("hello")

    assert step.action == "block"
    assert "failed closed" in step.detail.lower()
    assert "RuntimeError" in step.detail


def test_analyzer_error_fails_open_when_configured(monkeypatch):
    monkeypatch.setattr(
        presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg(fail_closed=False)}
    )

    class _Boom:
        def analyze(self, **kwargs):
            raise RuntimeError("model not loaded")

    monkeypatch.setattr(presidio_check, "_get_analyzer", lambda: _Boom())

    step = presidio_check.check_with_presidio("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_analyzer_error_never_leaks_the_raw_exception_message(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})

    class _Boom:
        def analyze(self, **kwargs):
            raise RuntimeError("sensitive internal detail: sk-fake-secret-123")

    monkeypatch.setattr(presidio_check, "_get_analyzer", lambda: _Boom())

    step = presidio_check.check_with_presidio("hello")

    assert "sk-fake-secret-123" not in step.detail
    assert "RuntimeError" in step.detail


def test_input_truncated_to_max_input_chars(monkeypatch):
    monkeypatch.setattr(
        presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg(max_input_chars=10)}
    )
    fake = _stub_analyzer(monkeypatch, [])

    presidio_check.check_with_presidio("a very long message that exceeds the configured max_input_chars limit")

    assert len(fake.calls[0]["text"]) == 10


def test_default_entity_allowlist_used_when_not_configured(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg(entities=[])})
    fake = _stub_analyzer(monkeypatch, [])

    presidio_check.check_with_presidio("hello")

    assert fake.calls[0]["entities"] == list(presidio_check._ALLOWED_ENTITIES)


def test_configured_entities_override_the_default_allowlist(monkeypatch):
    monkeypatch.setattr(
        presidio_check, "load_yaml_config",
        lambda name: {"presidio_check": _cfg(entities=["EMAIL_ADDRESS"])},
    )
    fake = _stub_analyzer(monkeypatch, [])

    presidio_check.check_with_presidio("hello")

    assert fake.calls[0]["entities"] == ["EMAIL_ADDRESS"]


def test_block_detail_never_contains_the_raw_matched_value(monkeypatch):
    # Same audit-log-leak concern pii.py's redact_pii() guards against: this
    # detail string reaches GET /admin/guardrail-analytics, visible to any
    # analytics-viewing role — only entity TYPES may appear, never the span.
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": _cfg()})
    text = "My passport number is 123456789"
    _stub_analyzer(monkeypatch, [RecognizerResult(entity_type="US_PASSPORT", start=23, end=32, score=0.9)])

    step = presidio_check.check_with_presidio(text)

    assert "123456789" not in step.detail
