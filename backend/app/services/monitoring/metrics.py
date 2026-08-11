import threading
import time
from collections import deque

# In-process, best-effort monitoring for the admin dashboard. Not persisted —
# a restart clears history, and with multiple uvicorn workers each worker
# only sees its own requests. That's an acceptable tradeoff for a dev-scale
# admin panel; if this needs to survive restarts or aggregate across workers
# later, back these with a real table instead of growing this module.
_MAX_SAMPLES = 1000
_LOCK = threading.Lock()
_LATENCIES: deque = deque(maxlen=_MAX_SAMPLES)
_TOKEN_USAGE: deque = deque(maxlen=_MAX_SAMPLES)
_RETRIEVAL_METRICS: deque = deque(maxlen=_MAX_SAMPLES)
_RETRIEVAL_ERRORS: deque = deque(maxlen=_MAX_SAMPLES)
_INGESTION_METRICS: deque = deque(maxlen=_MAX_SAMPLES)
_GUARDRAIL_EVENTS: deque = deque(maxlen=_MAX_SAMPLES)


def record_latency(endpoint: str, duration_ms: float) -> None:
    with _LOCK:
        _LATENCIES.append({"endpoint": endpoint, "duration_ms": duration_ms, "created_at": time.time()})


def record_retrieval_metrics(
    query: str, stages: dict[str, float], *, candidate_count: int, result_count: int, request_id: str | None = None
) -> None:
    """One record per real hybrid_search call (both POST /search and the chat
    agent's search_documents tool), so the dashboard can show a per-query
    breakdown of where retrieval time actually went, not just an endpoint-level
    average."""
    with _LOCK:
        _RETRIEVAL_METRICS.append({
            "query": query,
            "stages_ms": stages,
            "candidate_count": candidate_count,
            "result_count": result_count,
            "request_id": request_id,
            "created_at": time.time(),
        })


def record_retrieval_error(stage: str, error_type: str, *, request_id: str | None = None) -> None:
    """One record per infra failure (Qdrant/Postgres unavailable, reranker
    crash) caught along the retrieval path — counterpart to
    record_retrieval_metrics() for the failure case, since that function is
    only ever called on a successful hybrid_search() return."""
    with _LOCK:
        _RETRIEVAL_ERRORS.append({
            "stage": stage,
            "error_type": error_type,
            "request_id": request_id,
            "created_at": time.time(),
        })


def get_retrieval_errors() -> list[dict]:
    with _LOCK:
        return list(_RETRIEVAL_ERRORS)


def record_ingestion_metrics(filename: str, stages: dict[str, float], *, chunk_count: int) -> None:
    """One record per document upload, breaking down parse/chunk/
    tokenize/embed/entity/summarize/sparse-index time."""
    with _LOCK:
        _INGESTION_METRICS.append({
            "filename": filename,
            "stages_ms": stages,
            "chunk_count": chunk_count,
            "created_at": time.time(),
        })


def get_retrieval_metrics() -> list[dict]:
    with _LOCK:
        return list(_RETRIEVAL_METRICS)


def get_ingestion_metrics() -> list[dict]:
    with _LOCK:
        return list(_INGESTION_METRICS)


def record_token_usage(source: str, model: str, input_tokens: int, output_tokens: int) -> None:
    with _LOCK:
        _TOKEN_USAGE.append({
            "source": source,
            "model": model,
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "created_at": time.time(),
        })


def get_latencies() -> list[dict]:
    with _LOCK:
        return list(_LATENCIES)


def get_token_usage() -> list[dict]:
    with _LOCK:
        return list(_TOKEN_USAGE)


def record_guardrail_event(direction: str, check: str, action: str, detail: str) -> None:
    with _LOCK:
        _GUARDRAIL_EVENTS.append({
            "direction": direction,
            "check": check,
            "action": action,
            "detail": detail,
            "created_at": time.time(),
        })


def get_guardrail_events() -> list[dict]:
    with _LOCK:
        return list(_GUARDRAIL_EVENTS)
