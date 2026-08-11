"""gateway/claude_gateway.py — the error-classification fix behind the "no AI
model configured" bug report. Every failure used to raise a bare
GenerationError with no way to tell "key missing" from "key rejected" from
"provider overloaded" from "some other bug" apart — chat.py's fallback then
hardcoded the same "no AI model configured" sentence for all of them.
GenerationError now carries a `reason` (GenerationErrorReason); these tests
pin down exactly which reason each failure mode maps to.
"""

import anthropic
import httpx
import pytest

from app.core.config import settings
from app.gateway.claude_gateway import ClaudeGateway, GenerationError, classify_anthropic_error
from app.gateway.schemas import GenerateRequest, GenerationErrorReason, ModelTier, RetryPolicy


def _api_error(cls, status_code, body_type="invalid_request_error", message="boom"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status_code, request=req, json={"type": "error", "error": {"type": body_type, "message": message}})
    if cls is anthropic.APIConnectionError:
        return cls(request=req)
    return cls(message, response=resp, body=None)


@pytest.fixture(autouse=True)
def _restore_api_key():
    original = settings.anthropic_api_key
    yield
    settings.anthropic_api_key = original


# --------------------------------------------------------- classification ---

def test_authentication_error_classifies_as_auth_failed():
    exc = _api_error(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key")
    assert classify_anthropic_error(exc) is GenerationErrorReason.AUTH_FAILED


@pytest.mark.parametrize(
    "cls, status",
    [
        (anthropic.RateLimitError, 429),
        (anthropic.APIConnectionError, None),
        (anthropic.InternalServerError, 500),
        (anthropic.InternalServerError, 529),  # "overloaded" — the SDK maps every >=500 status here
    ],
)
def test_transient_provider_errors_classify_as_provider_unavailable(cls, status):
    exc = _api_error(cls, status) if status is not None else _api_error(cls, 0)
    assert classify_anthropic_error(exc) is GenerationErrorReason.PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    "cls, status",
    [
        (anthropic.BadRequestError, 400),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.NotFoundError, 404),
    ],
)
def test_other_api_errors_classify_as_provider_error(cls, status):
    exc = _api_error(cls, status)
    assert classify_anthropic_error(exc) is GenerationErrorReason.PROVIDER_ERROR


# ------------------------------------------------------------- no api key ---

def test_missing_api_key_raises_no_api_key_reason():
    settings.anthropic_api_key = ""
    gateway = ClaudeGateway()

    with pytest.raises(GenerationError) as exc_info:
        gateway.generate(GenerateRequest(agent_name="test", system="sys", messages=[{"role": "user", "content": "hi"}]))

    assert exc_info.value.reason is GenerationErrorReason.NO_API_KEY
    # The exception message is a static, safe sentence — never leaks whether
    # a key was present-but-invalid vs. genuinely absent, and can never
    # contain the key itself since there isn't one.
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)


def test_missing_api_key_on_langchain_model_path_raises_no_api_key_reason():
    settings.anthropic_api_key = ""
    gateway = ClaudeGateway()

    with pytest.raises(GenerationError) as exc_info:
        gateway.get_langchain_model(tier=ModelTier.FAST)

    assert exc_info.value.reason is GenerationErrorReason.NO_API_KEY


# ----------------------------------------------------------- capacity cap ---

def test_capacity_guard_raises_capacity_reason(monkeypatch):
    gateway = ClaudeGateway()
    # Force the semaphore to already be exhausted without needing to spin up
    # real concurrent threads.
    monkeypatch.setattr(gateway, "_get_semaphore", lambda: _AlwaysBusySemaphore())

    with pytest.raises(GenerationError) as exc_info:
        with gateway.capacity_guard():
            pass

    assert exc_info.value.reason is GenerationErrorReason.CAPACITY


class _AlwaysBusySemaphore:
    def acquire(self, blocking=False):
        return False

    def release(self):
        pass


# ------------------------------------------------- generate() end-to-end ---

def test_generate_wraps_anthropic_api_error_with_classified_reason(monkeypatch):
    settings.anthropic_api_key = "sk-ant-test-key-not-real"
    gateway = ClaudeGateway()

    class _FakeMessages:
        def create(self, **kwargs):
            raise _api_error(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key")

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(gateway, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(
        "app.gateway.retry_handler._policy",
        lambda: RetryPolicy(max_retries=0, base_delay_seconds=0, max_delay_seconds=0, retryable_errors=[]),
    )

    with pytest.raises(GenerationError) as exc_info:
        gateway.generate(GenerateRequest(agent_name="test", system="sys", messages=[{"role": "user", "content": "hi"}]))

    assert exc_info.value.reason is GenerationErrorReason.AUTH_FAILED
    # The API key configured on this gateway must never appear in the
    # resulting exception text, however it's later logged/rendered.
    assert "sk-ant-test-key-not-real" not in str(exc_info.value)


def test_default_generation_error_reason_is_internal():
    """A GenerationError constructed without an explicit reason (e.g. a call
    site nobody has classified yet) defaults to INTERNAL rather than
    silently implying "not configured" — the exact mislabeling this fix
    removes."""
    assert GenerationError("unclassified failure").reason is GenerationErrorReason.INTERNAL
