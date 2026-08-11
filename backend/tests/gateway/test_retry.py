import anthropic
import httpx
import pytest

from app.gateway import retry_handler
from app.gateway.schemas import RetryPolicy


def _connection_error():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


def _bad_request_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError("bad request", response=httpx.Response(400, request=req), body=None)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(retry_handler.time, "sleep", lambda _seconds: None)


def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _connection_error()
        return "ok"

    assert retry_handler.call_with_retry(flaky, agent_name="test") == "ok"
    assert calls["n"] == 3


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(
        retry_handler,
        "_policy",
        lambda: RetryPolicy(max_retries=2, base_delay_seconds=0, max_delay_seconds=0, retryable_errors=["APIConnectionError"]),
    )
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise _connection_error()

    with pytest.raises(anthropic.APIConnectionError):
        retry_handler.call_with_retry(always_fails, agent_name="test")
    assert calls["n"] == 3  # initial attempt + 2 retries


def test_non_retryable_error_fails_on_first_attempt():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise _bad_request_error()

    with pytest.raises(anthropic.BadRequestError):
        retry_handler.call_with_retry(bad, agent_name="test")
    assert calls["n"] == 1
