"""Guardrail-block escalation — tracks how often a user's messages get
blocked by the guardrail pipeline (input or output) within a rolling window
and temporarily locks them out once that crosses a threshold.

The gap this closes: services/llm_rbac/rate_limiter.py already throttles
REQUEST VOLUME per role (requests_per_minute, max_concurrent_requests), and
services/monitoring/metrics.py::record_guardrail_event() already logs every
block for the admin dashboard — but nothing reads that history back into a
live decision. A user who trips 10 injection/PII blocks in two minutes is
treated identically, on their 11th message, to a user who has never been
blocked. This module is that missing feedback loop: it doesn't replace rate
limiting (a well-behaved user well under their rate limit can still get
escalated here if every one of their messages is a guardrail violation) and
it doesn't replace any individual check (which still decide block/pass on
their own merits) — it only changes what happens to a user who accumulates
enough of those blocks.

In-process only, same constraint and same data-structure shape as
rate_limiter.py's token buckets: this app runs a single uvicorn worker with
no shared cache, so a plain dict is correct today and would need to move to
a shared store (e.g. Redis) if ever scaled to multiple workers/processes.
"""

import threading
import time
import uuid
from collections import defaultdict, deque

from app.core.errors import AppError
from app.core.yaml_config import load_yaml_config
from app.services.monitoring.metrics import record_guardrail_event

NAME = "guardrail_escalation"

_LOCK = threading.Lock()
_BLOCK_TIMESTAMPS: dict[uuid.UUID, deque] = defaultdict(deque)
_LOCKOUT_UNTIL: dict[uuid.UUID, float] = {}


def _config() -> dict:
    return load_yaml_config("guardrails.yaml").get("escalation", {})


def record_block(user_id: uuid.UUID) -> None:
    """Call once per turn where run_input_guardrails() or
    run_output_guardrails() actually blocked — not on every guardrail check,
    and not on a redact-only outcome (pii_redact on output never blocks, so
    it never counts toward this)."""
    cfg = _config()
    if not cfg.get("enabled", True):
        return

    window_seconds = cfg.get("window_seconds", 600)
    threshold = cfg.get("block_threshold", 5)
    now = time.monotonic()
    just_locked_out = False

    with _LOCK:
        history = _BLOCK_TIMESTAMPS[user_id]
        history.append(now)
        while history and now - history[0] > window_seconds:
            history.popleft()
        if len(history) >= threshold:
            lockout_seconds = cfg.get("lockout_seconds", 300)
            _LOCKOUT_UNTIL[user_id] = now + lockout_seconds
            history.clear()
            just_locked_out = True

    if just_locked_out:
        record_guardrail_event(
            "input", NAME, "block", f"User locked out after {threshold} guardrail blocks within {window_seconds}s"
        )


def check_escalation(user_id: uuid.UUID) -> None:
    """Raises AppError(429) if this user is currently locked out. Call this
    BEFORE running guardrails on a new message — a locked-out user's message
    should never reach the (comparatively expensive) guardrail pipeline or
    the planner at all."""
    cfg = _config()
    if not cfg.get("enabled", True):
        return

    now = time.monotonic()
    with _LOCK:
        until = _LOCKOUT_UNTIL.get(user_id)
        if until is not None and until <= now:
            del _LOCKOUT_UNTIL[user_id]
            until = None

    if until is None:
        return

    remaining = int(until - now) + 1
    raise AppError(
        429, "guardrail_escalation_lockout", f"Too many blocked messages recently — please try again in {remaining} seconds."
    )
