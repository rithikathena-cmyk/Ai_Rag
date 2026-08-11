"""app/db/resilience.py — Qdrant/Postgres retry wrappers built on the
generalized gateway/retry_handler.call_with_retry(). Mirrors
tests/gateway/test_retry.py's structure (retry-then-succeed,
give-up-after-max, non-retryable-fails-immediately) for the two new
predicates instead of re-testing call_with_retry() itself.
"""

import httpx
import pytest
import sqlalchemy.exc
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings
from app.db import resilience


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("app.gateway.retry_handler.time.sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _fast_retry_policy(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_retry_max_attempts", 2)
    monkeypatch.setattr(settings, "qdrant_retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "qdrant_retry_max_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "postgres_retry_max_attempts", 2)
    monkeypatch.setattr(settings, "postgres_retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "postgres_retry_max_delay_seconds", 0.0)


def _connect_error():
    return httpx.ConnectError("connection refused")


def test_qdrant_retries_connect_errors_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _connect_error()
        return "ok"

    assert resilience.qdrant_call_with_retry(flaky, agent_name="test") == "ok"
    assert calls["n"] == 2


def test_qdrant_gives_up_after_max_retries():
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise _connect_error()

    with pytest.raises(httpx.ConnectError):
        resilience.qdrant_call_with_retry(always_fails, agent_name="test")
    assert calls["n"] == 3  # initial attempt + 2 retries


def test_qdrant_5xx_is_retryable():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise UnexpectedResponse(status_code=503, reason_phrase="", content=b"", headers=None)
        return "ok"

    assert resilience.qdrant_call_with_retry(flaky, agent_name="test") == "ok"


def test_qdrant_4xx_is_not_retryable():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise UnexpectedResponse(status_code=400, reason_phrase="", content=b"", headers=None)

    with pytest.raises(UnexpectedResponse):
        resilience.qdrant_call_with_retry(bad, agent_name="test")
    assert calls["n"] == 1


def test_postgres_retries_operational_error_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("connection refused"))
        return "ok"

    assert resilience.postgres_call_with_retry(flaky, agent_name="test") == "ok"
    assert calls["n"] == 2


def test_postgres_integrity_error_is_not_retryable():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise sqlalchemy.exc.IntegrityError("stmt", {}, Exception("dup key"))

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        resilience.postgres_call_with_retry(bad, agent_name="test")
    assert calls["n"] == 1


class _FakeSession:
    """Found live under a concurrent /search smoke test: a real DBAPI error
    leaves a SQLAlchemy session's transaction in a failed/pending-rollback
    state, so retrying the same operation on that session without a
    rollback() in between raises PendingRollbackError instead of genuinely
    retrying — this fake tracks rollback() calls to prove the wrapper does
    one before each retry."""

    def __init__(self):
        self.rollback_calls = 0

    def rollback(self):
        self.rollback_calls += 1


def test_postgres_retry_rolls_back_session_between_attempts():
    db = _FakeSession()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("connection refused"))
        return "ok"

    assert resilience.postgres_call_with_retry(flaky, agent_name="test", db=db) == "ok"
    assert calls["n"] == 3
    # One rollback per failed attempt (2 failures before the 3rd succeeds) —
    # never on the final successful call.
    assert db.rollback_calls == 2


def test_postgres_retry_without_db_does_not_rollback():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("connection refused"))
        return "ok"

    # db=None (the default) — must not error trying to call .rollback() on
    # None; existing callers that don't pass db keep working unchanged.
    assert resilience.postgres_call_with_retry(flaky, agent_name="test") == "ok"
