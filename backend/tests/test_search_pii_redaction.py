"""routers/search.py's POST /search — proves PII redaction reaches the
actual API response JSON, not just the underlying search_with_reranking()
return value. Same lightweight-app convention as test_search_validation.py,
except search_with_reranking is left as the REAL function (only
hybrid_search/rerank stubbed underneath it) so this exercises the real
redaction boundary end-to-end, the same way it will actually run in
production.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import get_db
from app.routers import search as search_router
from app.services.auth.dependencies import get_current_user
from app.services.reranking import pipeline as reranking_pipeline
from app.services.retrieval.search import SearchHit


def _hit(text) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_index=0,
        parent_chunk_id=None, text=text, strategy="hybrid", score=0.9,
    )


class _FakeFilenameQuery:
    def filter(self, *a, **k):
        return self

    def all(self):
        return []


class _FakeDb:
    def query(self, *a, **k):
        return _FakeFilenameQuery()


def _make_app(monkeypatch, hit):
    app = FastAPI()
    app.include_router(search_router.router)

    fake_user = SimpleNamespace(id=uuid.uuid4(), role="user", department="engineering", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    # Unlike test_search_validation.py's `lambda: iter([None])` (fine there
    # since search_with_reranking is mocked to return zero hits, so
    # search.py's own DocumentModel filename lookup is never reached), this
    # test's hits are non-empty — search.py does query the DB for filenames,
    # so the override needs to actually be a generator function (matching
    # get_db's own shape) yielding an object that supports .query().filter().all() —
    # a plain lambda returning an iterator doesn't get unwrapped by FastAPI's
    # dependency injection the way a real generator dependency does.
    def _fake_get_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = _fake_get_db

    fake_decision = SimpleNamespace(role="user", knowledge_departments=("engineering",), max_concurrent_requests=None)
    monkeypatch.setattr(search_router, "authorize_llm_request", lambda *a, **k: fake_decision)
    monkeypatch.setattr(search_router, "record_search", lambda *a, **k: None)
    # search_with_reranking itself is NOT stubbed — only its own hybrid_search/
    # rerank dependencies are, so this test runs through the real redaction
    # boundary (services/reranking/pipeline.py) exactly as production does.
    monkeypatch.setattr(reranking_pipeline, "hybrid_search", lambda db, **k: ([hit], {"qdrant_ms": 1.0}))
    monkeypatch.setattr(reranking_pipeline, "rerank", lambda query, hits: hits)

    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


def test_search_response_redacts_pii_in_source_text(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    client = _make_app(monkeypatch, _hit("Contact John at john.smith@company.com or SSN 123-45-6789."))

    response = client.post("/search", json={"query": "contact info"})

    assert response.status_code == 200
    body = response.json()
    result_text = body["results"][0]["text"]
    assert "john.smith@company.com" not in result_text
    assert "123-45-6789" not in result_text
    assert "[REDACTED_EMAIL]" in result_text
    assert "[REDACTED_SSN]" in result_text


def test_search_response_metadata_untouched_by_redaction(monkeypatch):
    settings.guardrail_redact_pii = True
    hit = _hit("SSN 123-45-6789")
    client = _make_app(monkeypatch, hit)

    response = client.post("/search", json={"query": "q"})

    result = response.json()["results"][0]
    assert result["chunk_id"] == str(hit.chunk_id)
    assert result["document_id"] == str(hit.document_id)
    assert result["chunk_index"] == hit.chunk_index
    assert result["score"] == hit.score
    assert result["strategy"] == hit.strategy
