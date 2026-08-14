"""services/llm_rbac/quotas.py — daily/monthly budget checks. Not exercised
by tests/llm_rbac/test_policy_engine.py (it stubs quotas.check_budget to a
no-op via its autouse fixture, since that file is specifically about
permission/tier logic) — this file tests quotas.py itself, stubbing the
_get_or_create I/O boundary rather than requiring a running Postgres.
"""

import uuid

import pytest

from app.core.errors import AppError
from app.services.llm_rbac import quotas


class _FakeCounterRow:
    def __init__(self, request_count=0, tokens_used=0, cost_usd_used=0.0):
        self.request_count = request_count
        self.tokens_used = tokens_used
        self.cost_usd_used = cost_usd_used


# -------------------------------------------------------------- effective_quotas

def test_effective_quotas_returns_role_defaults_when_no_override_set():
    role_quotas = {"daily_requests": 10, "daily_tokens": 1000, "monthly_tokens": 10000, "monthly_cost_usd": 50}
    result = quotas.effective_quotas(role_quotas)
    assert result == role_quotas


def test_effective_quotas_daily_override_wins_over_role_default():
    role_quotas = {"daily_tokens": 1000, "monthly_tokens": 10000}
    result = quotas.effective_quotas(role_quotas, daily_token_limit_override=200)
    assert result["daily_tokens"] == 200
    assert result["monthly_tokens"] == 10000


def test_effective_quotas_monthly_override_wins_over_role_default():
    role_quotas = {"daily_tokens": 1000, "monthly_tokens": 10000}
    result = quotas.effective_quotas(role_quotas, monthly_token_limit_override=2000)
    assert result["daily_tokens"] == 1000
    assert result["monthly_tokens"] == 2000


def test_effective_quotas_override_can_apply_even_when_role_default_is_unlimited():
    # CEO/Admin's real llm_rbac.yaml quotas are null (unlimited) — an Admin
    # should still be able to cap a specific user's tokens despite that.
    result = quotas.effective_quotas({"daily_tokens": None, "monthly_tokens": None}, daily_token_limit_override=500)
    assert result["daily_tokens"] == 500
    assert result["monthly_tokens"] is None


def test_effective_quotas_leaves_non_token_fields_untouched():
    role_quotas = {"daily_requests": 10, "requests_per_minute": 5, "max_concurrent_requests": 2}
    result = quotas.effective_quotas(role_quotas, daily_token_limit_override=100, monthly_token_limit_override=1000)
    assert result["daily_requests"] == 10
    assert result["requests_per_minute"] == 5
    assert result["max_concurrent_requests"] == 2


def test_effective_quotas_does_not_mutate_input_dict():
    role_quotas = {"daily_tokens": 1000}
    quotas.effective_quotas(role_quotas, daily_token_limit_override=1)
    assert role_quotas == {"daily_tokens": 1000}


# ------------------------------------------------------------------ check_budget

def test_check_budget_passes_when_under_every_limit(monkeypatch):
    monkeypatch.setattr(quotas, "_get_or_create", lambda *a, **k: _FakeCounterRow())
    quotas.check_budget(
        db=None, user_id=uuid.uuid4(),
        role_quotas={"daily_requests": 10, "daily_tokens": 1000, "monthly_tokens": 10000, "monthly_cost_usd": 50},
    )


def test_check_budget_raises_429_when_daily_requests_exhausted(monkeypatch):
    monkeypatch.setattr(quotas, "_get_or_create", lambda *a, **k: _FakeCounterRow(request_count=10))
    with pytest.raises(AppError) as exc_info:
        quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={"daily_requests": 10})
    assert exc_info.value.status_code == 429


def test_check_budget_raises_429_when_daily_tokens_exhausted(monkeypatch):
    monkeypatch.setattr(quotas, "_get_or_create", lambda *a, **k: _FakeCounterRow(tokens_used=1000))
    with pytest.raises(AppError) as exc_info:
        quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={"daily_tokens": 1000})
    assert exc_info.value.status_code == 429


def test_check_budget_raises_429_when_monthly_tokens_exhausted(monkeypatch):
    monkeypatch.setattr(quotas, "_get_or_create", lambda *a, **k: _FakeCounterRow(tokens_used=5_000_000))
    with pytest.raises(AppError) as exc_info:
        quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={"monthly_tokens": 5_000_000})
    assert exc_info.value.status_code == 429


def test_check_budget_raises_429_when_monthly_cost_exhausted(monkeypatch):
    monkeypatch.setattr(quotas, "_get_or_create", lambda *a, **k: _FakeCounterRow(cost_usd_used=50.0))
    with pytest.raises(AppError) as exc_info:
        quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={"monthly_cost_usd": 50})
    assert exc_info.value.status_code == 429


def test_check_budget_skips_db_lookup_entirely_when_role_has_no_quotas(monkeypatch):
    # Matches admin's real llm_rbac.yaml entry: every quota field is null.
    calls = []
    monkeypatch.setattr(quotas, "_get_or_create", lambda *a, **k: calls.append(1) or _FakeCounterRow())
    quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={})
    assert calls == []


# ---------------------------------------------------- Postgres resilience

def test_check_budget_maps_postgres_failure_to_503(monkeypatch):
    # Found live under concurrent /search load: a Postgres timeout/contention
    # error hitting role_usage_counters previously surfaced as a raw,
    # unclassified 500 — this is the authorization-path counterpart to
    # services/retrieval/search.py's Qdrant/Postgres error mapping.
    import sqlalchemy.exc

    from app.core.config import settings

    monkeypatch.setattr(settings, "postgres_retry_max_attempts", 0)

    def always_fails(*a, **k):
        raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("canceling statement due to statement timeout"))

    monkeypatch.setattr(quotas, "_get_or_create", always_fails)

    with pytest.raises(AppError) as exc_info:
        quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={"daily_requests": 10})
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "rbac_check_unavailable"


def test_check_budget_retries_transient_postgres_failure_then_succeeds(monkeypatch):
    import sqlalchemy.exc

    from app.core.config import settings

    monkeypatch.setattr(settings, "postgres_retry_max_attempts", 2)
    monkeypatch.setattr(settings, "postgres_retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "postgres_retry_max_delay_seconds", 0.0)
    monkeypatch.setattr("app.gateway.retry_handler.time.sleep", lambda _s: None)

    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("connection refused"))
        return _FakeCounterRow()

    monkeypatch.setattr(quotas, "_get_or_create", flaky)

    quotas.check_budget(db=None, user_id=uuid.uuid4(), role_quotas={"daily_requests": 10})
    assert calls["n"] == 2
