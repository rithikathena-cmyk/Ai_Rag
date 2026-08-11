"""Retry wrappers for the two backing stores the retrieval path depends on —
Qdrant and Postgres. Built on top of gateway/retry_handler.py's generalized
call_with_retry() rather than a second backoff implementation; policy comes
from app/core/config.py's qdrant_*/postgres_* settings (Settings.Config
already covers this — no new config mechanism).

Retryable = transient/connection-shaped failures where retrying might help
(timeouts, connection errors, 5xx). Not retryable = anything that means the
request itself was bad (4xx, integrity errors, bad queries) — retrying those
just wastes time before failing anyway.
"""

import sqlalchemy.exc
from httpx import ConnectError, ConnectTimeout, HTTPError, PoolTimeout, ReadTimeout, RemoteProtocolError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import settings
from app.gateway.retry_handler import call_with_retry
from app.gateway.schemas import RetryPolicy

# Exposed so callers (e.g. services/retrieval/search.py) can classify a
# caught exception as "an infra failure in this store" without re-importing
# every underlying exception type themselves.
QDRANT_EXCEPTIONS = (HTTPError, ResponseHandlingException, UnexpectedResponse)
POSTGRES_EXCEPTIONS = (sqlalchemy.exc.SQLAlchemyError,)


def _qdrant_retryable(exc: Exception) -> bool:
    if isinstance(exc, (ConnectError, ConnectTimeout, ReadTimeout, PoolTimeout, RemoteProtocolError, ResponseHandlingException)):
        return True
    if isinstance(exc, UnexpectedResponse):
        return exc.status_code >= 500
    return False


def _postgres_retryable(exc: Exception) -> bool:
    # OperationalError covers connection-refused/lost/timeout; TimeoutError is
    # the pool itself being exhausted. Anything else (IntegrityError,
    # ProgrammingError, DataError, ...) is a bad query/data issue a retry
    # can't fix.
    return isinstance(exc, (sqlalchemy.exc.OperationalError, sqlalchemy.exc.TimeoutError))


def qdrant_call_with_retry(fn, *, agent_name: str):
    policy = RetryPolicy(
        max_retries=settings.qdrant_retry_max_attempts,
        base_delay_seconds=settings.qdrant_retry_base_delay_seconds,
        max_delay_seconds=settings.qdrant_retry_max_delay_seconds,
    )
    return call_with_retry(fn, agent_name=agent_name, policy=policy, is_retryable=_qdrant_retryable)


def postgres_call_with_retry(fn, *, agent_name: str, db=None):
    """`db`, when given, is rolled back after any Postgres exception before a
    retry is attempted. Required for correctness, not just cleanliness: once
    a DBAPI error occurs, SQLAlchemy leaves the session's transaction in a
    failed/pending-rollback state — retrying `fn()` on that same session
    without rolling back first raises PendingRollbackError immediately
    instead of genuinely retrying the original operation."""

    def wrapped():
        try:
            return fn()
        except POSTGRES_EXCEPTIONS:
            if db is not None:
                db.rollback()
            raise

    policy = RetryPolicy(
        max_retries=settings.postgres_retry_max_attempts,
        base_delay_seconds=settings.postgres_retry_base_delay_seconds,
        max_delay_seconds=settings.postgres_retry_max_delay_seconds,
    )
    return call_with_retry(wrapped, agent_name=agent_name, policy=policy, is_retryable=_postgres_retryable)
