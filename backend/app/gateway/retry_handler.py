import logging
import random
import time
from typing import Callable, TypeVar

import anthropic

from app.core.yaml_config import load_yaml_config
from app.gateway.schemas import RetryPolicy

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_POLICY = RetryPolicy(
    max_retries=3,
    base_delay_seconds=1.0,
    max_delay_seconds=20.0,
    retryable_errors=["RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"],
)


def _policy() -> RetryPolicy:
    cfg = load_yaml_config("llm.yaml").get("retry")
    if not cfg:
        return _DEFAULT_POLICY
    return RetryPolicy(
        max_retries=cfg.get("max_retries", _DEFAULT_POLICY.max_retries),
        base_delay_seconds=cfg.get("base_delay_seconds", _DEFAULT_POLICY.base_delay_seconds),
        max_delay_seconds=cfg.get("max_delay_seconds", _DEFAULT_POLICY.max_delay_seconds),
        retryable_errors=cfg.get("retryable_errors", _DEFAULT_POLICY.retryable_errors),
    )


def _is_retryable(exc: Exception, retryable_names: list[str]) -> bool:
    return type(exc).__name__ in retryable_names


def call_with_retry(
    fn: Callable[[], T],
    *,
    agent_name: str = "",
    policy: RetryPolicy | None = None,
    is_retryable: Callable[[Exception], bool] | None = None,
) -> T:
    """Calls fn(), retrying transient failures with exponential backoff +
    jitter (capped at max_delay_seconds). Non-retryable errors propagate on
    the first attempt.

    Default behavior (no policy/is_retryable — every existing caller) is
    unchanged from before this was generalized: policy comes from
    backend/config/llm.yaml's `retry` block, and only `anthropic.APIError`
    subclasses matching policy.retryable_errors are retried.

    Callers outside the Anthropic Gateway (e.g. app/db/resilience.py for
    Qdrant/Postgres) pass their own `policy` and `is_retryable` predicate —
    `is_retryable` receives the raw exception, not just its type name, so it
    can inspect things like an HTTP status code.
    """
    policy = policy or _policy()
    # Default mode (is_retryable=None) only ever sees anthropic.APIError —
    # matches every existing caller's behavior exactly. A supplied
    # is_retryable widens the catch to any Exception, since non-Gateway
    # callers (Qdrant/Postgres) raise their own exception types.
    catch = Exception if is_retryable is not None else anthropic.APIError
    attempt = 0
    while True:
        try:
            return fn()
        except catch as exc:
            attempt += 1
            retryable = is_retryable(exc) if is_retryable is not None else _is_retryable(exc, policy.retryable_errors)
            if not retryable or attempt > policy.max_retries:
                raise
            delay = min(policy.base_delay_seconds * (2 ** (attempt - 1)), policy.max_delay_seconds)
            delay += random.uniform(0, delay * 0.1)
            logger.warning(
                "Retry %s/%s for %s after %s: %s (sleeping %.2fs)",
                attempt, policy.max_retries, agent_name or "unknown-agent", type(exc).__name__, exc, delay,
            )
            time.sleep(delay)
