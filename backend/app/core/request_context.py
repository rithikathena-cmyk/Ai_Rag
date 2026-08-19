"""The app-wide request-id ContextVar — extracted from app/main.py into its
own module specifically so service-layer code (services/audit/logger.py
call sites, services/guardrails/orchestrator_graph.py, routers) can read the
current request's correlation ID without importing app.main itself, which
would be circular (main.py imports every router at module level; a router
importing back from main.py fails at import time).

app/main.py's observability_middleware still owns setting/resetting this
ContextVar per request and mounting the logging filter that reads it — this
module only owns the ContextVar's storage and the public read accessor, so
there is exactly one request_id source of truth either way.
"""

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_current_request_id() -> str:
    """Returns the current request's correlation ID, or "-" outside a
    request context — same default app/main.py's own logging filter has
    always shown for an unrequested log line."""
    return request_id_ctx.get()
