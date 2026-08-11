"""services/monitoring/metrics.py::record_retrieval_error/get_retrieval_errors
— the failure-case counterpart to record_retrieval_metrics(), added so the
admin dashboard can see infra failures along the retrieval path, not just
successful-query timings.
"""

from app.services.monitoring import metrics


def test_record_and_get_retrieval_errors_roundtrip():
    metrics._RETRIEVAL_ERRORS.clear()

    metrics.record_retrieval_error("qdrant_ms", "ConnectError", request_id="req-1")
    metrics.record_retrieval_error("rerank", "RuntimeError", request_id="req-2")

    errors = metrics.get_retrieval_errors()
    assert len(errors) == 2
    assert errors[0]["stage"] == "qdrant_ms"
    assert errors[0]["error_type"] == "ConnectError"
    assert errors[0]["request_id"] == "req-1"
    assert errors[1]["stage"] == "rerank"


def test_record_retrieval_error_without_request_id():
    metrics._RETRIEVAL_ERRORS.clear()

    metrics.record_retrieval_error("filter_ms", "OperationalError")

    errors = metrics.get_retrieval_errors()
    assert errors[0]["request_id"] is None
