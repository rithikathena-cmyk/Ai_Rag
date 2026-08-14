"""services/guardrails/output.py — system-prompt-fragment and
credential-shaped-value leak detection on model output."""

import pytest

from app.core.config import settings
from app.services.guardrails.output import check_system_prompt_leak


@pytest.fixture(autouse=True)
def _enabled():
    original = settings.guardrail_block_system_prompt_leak
    settings.guardrail_block_system_prompt_leak = True
    yield
    settings.guardrail_block_system_prompt_leak = original


def test_disabled_check_passes_through():
    settings.guardrail_block_system_prompt_leak = False
    assert check_system_prompt_leak("sk-abcdefghijklmnopqrstuvwxyz").action == "pass"


def test_normal_reply_passes():
    step = check_system_prompt_leak("The leave policy allows 20 days of paid leave per year.")
    assert step.action == "pass"


def test_anthropic_style_key_blocks():
    step = check_system_prompt_leak("Here's your key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890")
    assert step.action == "block"


def test_aws_access_key_blocks():
    step = check_system_prompt_leak("Use AKIAABCDEFGHIJKLMNOP as the access key.")
    assert step.action == "block"


def test_jwt_shaped_value_blocks():
    step = check_system_prompt_leak(
        "Token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGhpc2lzYXNpZ25hdHVyZQ"
    )
    assert step.action == "block"


def test_generic_advice_about_env_vars_does_not_block():
    """Educational/generic advice, with no actual secret value present,
    must not be treated as a leak."""
    step = check_system_prompt_leak("You can set your API key in the .env file as ANTHROPIC_API_KEY=your-key-here.")
    assert step.action == "pass"


def test_github_token_blocks():
    step = check_system_prompt_leak("Use ghp_abcdefghijklmnopqrstuvwxyzABCDEFGH1234 to authenticate.")
    assert step.action == "block"


def test_slack_token_blocks():
    # Built via concatenation, not a literal, so GitHub's push-protection
    # secret scanner (which pattern-matches raw file text) doesn't flag this
    # obviously-synthetic fixture as a real Slack token.
    fake_token = "xoxb-" + "1234567890" + "-abcdefghijklmnop"
    step = check_system_prompt_leak(f"Here's the bot token: {fake_token}")
    assert step.action == "block"


def test_google_api_key_blocks():
    step = check_system_prompt_leak(f"Google Maps key: AIza{'S' * 35}")
    assert step.action == "block"


def test_stripe_secret_key_blocks():
    # Same concatenation trick as the Slack fixture above — avoids tripping
    # GitHub's push-protection scanner on this synthetic value.
    fake_key = "sk_live_" + "abcdefghijklmnopqrstuvwx"
    step = check_system_prompt_leak(f"Stripe secret: {fake_key}")
    assert step.action == "block"


def test_pem_private_key_block_blocks():
    step = check_system_prompt_leak("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...")
    assert step.action == "block"


def test_generic_mention_of_github_or_slack_tokens_does_not_block():
    """Same educational-advice principle as the env-var case above — talking
    ABOUT a token type with no actual token value present must not block."""
    step = check_system_prompt_leak("You can generate a GitHub personal access token from your account settings.")
    assert step.action == "pass"
