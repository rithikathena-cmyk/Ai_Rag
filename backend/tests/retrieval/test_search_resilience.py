"""services/retrieval/search.py::hybrid_search — infra-failure mapping added
by the /search hardening plan. Stubs the Qdrant/embedding/DB boundaries
directly (matching this suite's established convention, e.g.
tests/retrieval/test_parent_context.py) rather than requiring a live Qdrant.
"""

import httpx
import pytest
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings
from app.core.errors import AppError
from app.services.monitoring import metrics
from app.services.retrieval import search


class _FakeDb:
    pass


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("app.gateway.retry_handler.time.sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _fast_retry_policy(monkeypatch):
    monkeypatch.setattr(settings, "qdrant_retry_max_attempts", 1)
    monkeypatch.setattr(settings, "qdrant_retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(settings, "qdrant_retry_max_delay_seconds", 0.0)


@pytest.fixture(autouse=True)
def _stub_infra(monkeypatch):
    monkeypatch.setattr(search, "resolve_document_ids", lambda db, **k: None)
    monkeypatch.setattr(search, "ensure_collection", lambda: None)
    monkeypatch.setattr(search, "embed_query", lambda q: [0.1, 0.2])


def _fake_client(query_points):
    class _Client:
        def query_points(self, **kwargs):
            return query_points(**kwargs)

    return _Client()


def test_qdrant_connection_error_maps_to_503(monkeypatch):
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(search, "get_qdrant_client", lambda: _fake_client(flaky))

    with pytest.raises(AppError) as exc_info:
        search.hybrid_search(_FakeDb(), query="q", mode="semantic", allow_unfiltered=True, request_id="req-1")

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "search_unavailable"
    assert calls["n"] == 2  # initial attempt + 1 retry (qdrant_retry_max_attempts=1)

    errors = metrics.get_retrieval_errors()
    assert any(e["request_id"] == "req-1" and e["stage"] == "qdrant_ms" for e in errors)


def test_qdrant_4xx_not_retried(monkeypatch):
    calls = {"n": 0}

    def bad(**kwargs):
        calls["n"] += 1
        raise UnexpectedResponse(status_code=400, reason_phrase="", content=b"", headers=None)

    monkeypatch.setattr(search, "get_qdrant_client", lambda: _fake_client(bad))

    with pytest.raises(AppError) as exc_info:
        search.hybrid_search(_FakeDb(), query="q", mode="semantic", allow_unfiltered=True)

    assert exc_info.value.status_code == 503
    assert calls["n"] == 1  # non-retryable — no retry attempted
