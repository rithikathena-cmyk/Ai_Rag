"""services/guardrails/gliner_check.py — the GLiNER-based semantic PII
check. Mocks gliner_check._get_model() directly (rather than requiring the
real GLiNER checkpoint to load per test) so this suite stays fast and
deterministic, matching this package's established convention of stubbing
the I/O/model boundary — see test_presidio_check.py.
"""

import pytest

from app.services.guardrails import gliner_check


def _cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.6, "max_input_chars": 2000, "labels": []}
    base.update(overrides)
    return base


class _FakeModel:
    def __init__(self, entities):
        self._entities = entities
        self.calls = []

    def predict_entities(self, text, labels, threshold):
        self.calls.append({"text": text, "labels": labels, "threshold": threshold})
        return self._entities


@pytest.fixture(autouse=True)
def _reset_model_cache():
    gliner_check._model = None
    yield
    gliner_check._model = None


def _stub_model(monkeypatch, entities):
    fake = _FakeModel(entities)
    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: fake)
    return fake


def test_disabled_is_a_no_op_and_never_builds_the_model(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(enabled=False)})

    def _unexpected(model_name):
        raise AssertionError("_get_model must not be called when the check is disabled")

    monkeypatch.setattr(gliner_check, "_get_model", _unexpected)

    step = gliner_check.check_with_gliner("hello")

    assert step.action == "pass"
    assert "disabled" in step.detail.lower()


def test_empty_input_short_circuits_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg()})

    def _unexpected(model_name):
        raise AssertionError("_get_model must not be called for empty input")

    monkeypatch.setattr(gliner_check, "_get_model", _unexpected)

    step = gliner_check.check_with_gliner("   ")

    assert step.action == "pass"


def test_pass_when_no_entities_detected(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg()})
    _stub_model(monkeypatch, [])

    step = gliner_check.check_with_gliner("What is the annual leave accrual rate?")

    assert step.action == "pass"
    assert step.name == "gliner_check"


def test_block_on_detected_address(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg()})
    _stub_model(
        monkeypatch,
        [{"start": 0, "end": 16, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.93}],
    )

    step = gliner_check.check_with_gliner("42 Oakwood Lane is where they live.")

    assert step.action == "block"
    assert "physical address" in step.detail


def test_block_detail_never_contains_the_raw_matched_value(monkeypatch):
    # Same audit-log-leak concern presidio_check.py/pii.py guard against —
    # only the label may appear in detail, never the matched span text.
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg()})
    _stub_model(
        monkeypatch,
        [{"start": 0, "end": 16, "text": "42 Oakwood Lane", "label": "physical address", "score": 0.93}],
    )

    step = gliner_check.check_with_gliner("42 Oakwood Lane is where they live.")

    assert "42 Oakwood Lane" not in step.detail


def test_default_label_set_used_when_not_configured(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(labels=[])})
    fake = _stub_model(monkeypatch, [])

    gliner_check.check_with_gliner("hello")

    assert fake.calls[0]["labels"] == list(gliner_check._DEFAULT_LABELS)


def test_default_label_set_excludes_person_and_organization():
    # The specific false-positive trap this module's docstring documents —
    # this app's HR/manufacturing content routinely names real employees;
    # a name-catching label would make ordinary queries unusable.
    labels_lower = {label.lower() for label in gliner_check._DEFAULT_LABELS}
    assert not any("person" in label or "name" in label or "organization" in label for label in labels_lower)


def test_configured_labels_override_the_default_set(monkeypatch):
    monkeypatch.setattr(
        gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(labels=["custom label"])}
    )
    fake = _stub_model(monkeypatch, [])

    gliner_check.check_with_gliner("hello")

    assert fake.calls[0]["labels"] == ["custom label"]


def test_input_truncated_to_max_input_chars(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(max_input_chars=10)})
    fake = _stub_model(monkeypatch, [])

    gliner_check.check_with_gliner("a very long message that exceeds the configured max_input_chars limit")

    assert len(fake.calls[0]["text"]) == 10


def test_model_error_fails_closed_by_default(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg()})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("model not loaded")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    step = gliner_check.check_with_gliner("hello")

    assert step.action == "block"
    assert "failed closed" in step.detail.lower()


def test_model_error_fails_open_when_configured(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg(fail_closed=False)})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("model not loaded")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    step = gliner_check.check_with_gliner("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_model_error_never_leaks_the_raw_exception_message(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": _cfg()})

    class _Boom:
        def predict_entities(self, *a, **k):
            raise RuntimeError("sensitive internal detail: sk-fake-secret-123")

    monkeypatch.setattr(gliner_check, "_get_model", lambda model_name: _Boom())

    step = gliner_check.check_with_gliner("hello")

    assert "sk-fake-secret-123" not in step.detail
    assert "RuntimeError" in step.detail
