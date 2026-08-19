"""services/guardrails/secrets.py — credential-shaped value detection
(input-side check_secrets) and redaction (retrieval-side redact_secrets)."""

import pytest

from app.services.guardrails import secrets


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(secrets, "load_yaml_config", lambda name: {"secret_detection": {"enabled": True}})


def test_disabled_check_passes_through(monkeypatch):
    monkeypatch.setattr(secrets, "load_yaml_config", lambda name: {"secret_detection": {"enabled": False}})
    assert secrets.check_secrets("sk-abcdefghijklmnopqrstuvwxyz123456").action == "pass"


# Test credential patterns (fake/non-functional values only)
_CREDENTIALS = [
    "sk-" + "a" * 36,  # OpenAI format
    "AKIA" + "A" * 16,  # AWS format
    "ghp_" + "a" * 38,  # GitHub format
    "xoxb-1234567890-abcdefghij",  # Slack format
    "AIzaSy" + "A" * 51,  # Google API format
    "sk_live_" + "a" * 24,  # Stripe format
    "-----BEGIN RSA PRIVATE KEY-----",  # PEM format
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "postgres://user:pass@host:5432/db",  # Database URL pattern
]


@pytest.mark.parametrize("value", _CREDENTIALS)
def test_credential_shapes_block(value):
    step = secrets.check_secrets(f"Here is my key: {value}")
    assert step.action == "block"


def test_block_detail_never_echoes_the_matched_value():
    step = secrets.check_secrets("my key is AKIAABCDEFGHIJKLMNOP")
    assert "AKIA" not in step.detail


def test_generic_mention_of_api_keys_does_not_block():
    step = secrets.check_secrets("Where do I find my API key in the settings page?")
    assert step.action == "pass"


def test_stated_password_blocks():
    step = secrets.check_secrets("my password is hunter2correcthorsebattery, save it")
    assert step.action == "block"
    assert "PASSWORD" in step.detail


def test_stated_password_detail_never_echoes_the_value():
    step = secrets.check_secrets("my password is hunter2correcthorsebattery, save it")
    assert "hunter2correcthorsebattery" not in step.detail


@pytest.mark.parametrize("phrasing", ["passwd: hunter2correcthorsebattery", "pwd=hunter2correcthorsebattery"])
def test_password_synonyms_and_separators_block(phrasing):
    assert secrets.check_secrets(phrasing).action == "block"


def test_password_is_case_insensitive():
    assert secrets.check_secrets("Password IS Hunter2CorrectHorseBattery").action == "block"


def test_asking_about_a_password_does_not_block():
    """A question has no value following the keyword — mirrors
    test_generic_mention_of_api_keys_does_not_block above for the same
    'keyword alone must not block' rule."""
    assert secrets.check_secrets("Where do I find my password in the settings page?").action == "pass"
    assert secrets.check_secrets("Is my password secure enough?").action == "pass"


def test_ordinary_message_passes():
    step = secrets.check_secrets("What is our leave management policy?")
    assert step.action == "pass"


def test_redact_secrets_replaces_the_value_and_reports_found():
    text = "Config: AKIAABCDEFGHIJKLMNOP is the access key"
    redacted, found = secrets.redact_secrets(text)
    assert found is True
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_redact_secrets_is_a_noop_on_clean_text():
    text = "This SOP covers machine shutdown procedures."
    redacted, found = secrets.redact_secrets(text)
    assert found is False
    assert redacted == text


def test_redact_secrets_handles_multiple_matches():
    text = "Old key AKIAABCDEFGHIJKLMNOP, new key AKIAZYXWVUTSRQPONMLK"
    redacted, found = secrets.redact_secrets(text)
    assert found is True
    assert redacted.count("[REDACTED_SECRET]") == 2
