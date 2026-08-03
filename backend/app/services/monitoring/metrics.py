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


def record_latency(endpoint: str, duration_ms: float) -> None:
    with _LOCK:
        _LATENCIES.append({"endpoint": endpoint, "duration_ms": duration_ms, "created_at": time.time()})


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
