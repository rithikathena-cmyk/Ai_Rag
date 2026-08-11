"""In-process, best-effort per-document ingestion progress, for live polling
from the frontend while a /documents/upload request is still in flight.
Same tradeoffs as monitoring/metrics.py: not persisted, single-worker only."""

import threading
import time

_LOCK = threading.Lock()
_PROGRESS: dict[str, dict] = {}
_MAX_ENTRIES = 200

STAGES = ["parse", "summarize", "entity", "chunk", "embed", "sparse", "sparse_index", "qdrant_upsert"]


def start(document_id: str, filename: str) -> None:
    with _LOCK:
        if len(_PROGRESS) >= _MAX_ENTRIES:
            oldest_id = min(_PROGRESS, key=lambda k: _PROGRESS[k]["started_at"])
            del _PROGRESS[oldest_id]
        _PROGRESS[document_id] = {
            "filename": filename,
            "status": "running",
            "current_stage": None,
            "started_at": time.time(),
            "stages": {s: {"status": "pending", "elapsed_ms": None} for s in STAGES},
        }


def begin_stage(document_id: str, stage: str) -> None:
    with _LOCK:
        p = _PROGRESS.get(document_id)
        if p is None:
            return
        p["current_stage"] = stage
        p["stages"][stage] = {"status": "running", "elapsed_ms": None}


def end_stage(document_id: str, stage: str, elapsed_ms: float) -> None:
    with _LOCK:
        p = _PROGRESS.get(document_id)
        if p is None:
            return
        p["stages"][stage] = {"status": "done", "elapsed_ms": elapsed_ms}


def finish(document_id: str, status: str) -> None:
    with _LOCK:
        p = _PROGRESS.get(document_id)
        if p is None:
            return
        p["status"] = status
        p["current_stage"] = None


def get(document_id: str) -> dict | None:
    with _LOCK:
        p = _PROGRESS.get(document_id)
        return None if p is None else {**p, "stages": dict(p["stages"])}
