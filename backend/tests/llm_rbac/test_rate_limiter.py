"""services/llm_rbac/rate_limiter.py — the enforcement point for
requests_per_minute/max_concurrent_requests, both already modeled per-role
in llm_rbac.yaml but previously unenforced anywhere."""

import uuid

import pytest

from app.core.errors import AppError
from app.services.llm_rbac import rate_limiter


@pytest.fixture(autouse=True)
def _clear_state():
    rate_limiter._BUCKETS.clear()
    rate_limiter._CONCURRENCY.clear()
    yield
    rate_limiter._BUCKETS.clear()
    rate_limiter._CONCURRENCY.clear()


def test_none_limit_is_unlimited():
    user_id = uuid.uuid4()
    for _ in range(1000):
        rate_limiter.check_rate_limit(user_id, None)  # must never raise


def test_burst_up_to_capacity_then_blocks(monkeypatch):
    user_id = uuid.uuid4()
    now = [1000.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: now[0])

    for _ in range(5):
        rate_limiter.check_rate_limit(user_id, 5)  # capacity=5, all consumed with no time passing

    with pytest.raises(AppError) as exc_info:
        rate_limiter.check_rate_limit(user_id, 5)
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "rate_limited"


def test_bucket_refills_over_time(monkeypatch):
    user_id = uuid.uuid4()
    now = [1000.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: now[0])

    for _ in range(5):
        rate_limiter.check_rate_limit(user_id, 5)  # capacity=5 (5 req/min => refill 1 every 12s)
    with pytest.raises(AppError):
        rate_limiter.check_rate_limit(user_id, 5)

    now[0] += 12.0  # one token's worth of time passes
    rate_limiter.check_rate_limit(user_id, 5)  # succeeds now


def test_different_users_have_independent_buckets(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: now[0])
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    for _ in range(3):
        rate_limiter.check_rate_limit(user_a, 3)
    with pytest.raises(AppError):
        rate_limiter.check_rate_limit(user_a, 3)

    rate_limiter.check_rate_limit(user_b, 3)  # unaffected by user_a's exhausted bucket


def test_concurrency_guard_none_is_unlimited():
    user_id = uuid.uuid4()
    guards = [rate_limiter.ConcurrencyGuard(user_id, None) for _ in range(50)]
    for g in guards:
        g.__enter__()
    for g in guards:
        g.__exit__(None, None, None)


def test_concurrency_guard_blocks_the_nth_plus_one_request():
    user_id = uuid.uuid4()
    g1 = rate_limiter.ConcurrencyGuard(user_id, 2)
    g2 = rate_limiter.ConcurrencyGuard(user_id, 2)
    g1.__enter__()
    g2.__enter__()

    g3 = rate_limiter.ConcurrencyGuard(user_id, 2)
    with pytest.raises(AppError) as exc_info:
        g3.__enter__()
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "concurrency_limit_exceeded"

    g1.__exit__(None, None, None)
    g3.__enter__()  # a slot freed up, this now succeeds


def test_concurrency_guard_releases_on_exception():
    user_id = uuid.uuid4()
    with pytest.raises(RuntimeError):
        with rate_limiter.ConcurrencyGuard(user_id, 1):
            raise RuntimeError("boom")

    # slot was released despite the exception — a second request succeeds
    with rate_limiter.ConcurrencyGuard(user_id, 1):
        pass
