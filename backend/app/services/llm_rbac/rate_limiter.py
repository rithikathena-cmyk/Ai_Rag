"""In-process request-rate and concurrency limiter for LLM-RBAC governed
endpoints (routers/search.py, routers/chat.py — both funnel through
engine.py::authorize_llm_request). llm_rbac.yaml already models
requests_per_minute/max_concurrent_requests per role (see RoleConfig.quotas)
— this module is the enforcement point that was missing; nothing here
invents new config.

In-process only: the app runs as a single uvicorn worker per instance (no
--workers in the Dockerfile CMD) and docker-compose.yml has no Redis/queue
service, so a plain in-memory dict is correct today. If this is ever scaled
to multiple workers/processes, these counters must move to a shared store
(e.g. Redis) — each process would otherwise track an independent bucket,
silently multiplying the effective per-user limit by worker count.
"""

import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from app.core.errors import AppError


@dataclass
class _TokenBucket:
    capacity: float
    tokens: float
    refill_per_second: float
    # Deferred lookup (lambda, not `time.monotonic` directly) so this reads
    # time.monotonic at instantiation time via the module attribute, the same
    # way try_consume() below does — matters for tests that monkeypatch
    # rate_limiter.time.monotonic, since a bare `field(default_factory=
    # time.monotonic)` would bind the real function once at class-definition
    # time, before any monkeypatch could apply.
    last_refill: float = field(default_factory=lambda: time.monotonic())

    def try_consume(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.refill_per_second)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


_LOCK = threading.Lock()
_BUCKETS: dict[uuid.UUID, _TokenBucket] = {}
_CONCURRENCY: dict[uuid.UUID, int] = defaultdict(int)


def check_rate_limit(user_id: uuid.UUID, requests_per_minute: int | None) -> None:
    """No-op for a role with no configured limit (requests_per_minute=None,
    e.g. admin in llm_rbac.yaml). Otherwise consumes one token from that
    user's bucket or raises AppError(429)."""
    if requests_per_minute is None:
        return
    with _LOCK:
        bucket = _BUCKETS.get(user_id)
        if bucket is None or bucket.capacity != requests_per_minute:
            # Capacity mismatch means the role's configured limit changed
            # (or this is the user's first request) — reset to a fresh,
            # fully-topped-up bucket at the new capacity rather than
            # carrying over a stale token count.
            bucket = _TokenBucket(
                capacity=requests_per_minute, tokens=requests_per_minute, refill_per_second=requests_per_minute / 60.0
            )
            _BUCKETS[user_id] = bucket
        allowed = bucket.try_consume()
    if not allowed:
        raise AppError(429, "rate_limited", "Too many requests — please slow down and try again shortly.")


class ConcurrencyGuard:
    """Context manager bounding how many requests a single user may have
    in flight at once, per role.max_concurrent_requests. Must wrap the
    actual work (not just the authorize step), since concurrency is about
    what's in flight for its full duration, not a point-in-time check."""

    def __init__(self, user_id: uuid.UUID, max_concurrent: int | None):
        self.user_id = user_id
        self.max_concurrent = max_concurrent

    def __enter__(self):
        if self.max_concurrent is not None:
            with _LOCK:
                if _CONCURRENCY[self.user_id] >= self.max_concurrent:
                    raise AppError(
                        429, "concurrency_limit_exceeded",
                        "Too many concurrent requests for your role — please wait for an earlier one to finish.",
                    )
                _CONCURRENCY[self.user_id] += 1
        return self

    def __exit__(self, *exc_info):
        if self.max_concurrent is not None:
            with _LOCK:
                _CONCURRENCY[self.user_id] -= 1
