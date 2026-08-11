"""routers/search.py::SearchRequest/SearchFilters — the tightened Pydantic
bounds added by the /search hardening plan (query max_length, top_k ge/le,
document_ids max_length). Mounts just the search router with the auth/DB
dependencies overridden, matching tests/test_rbac.py's established
lightweight-app convention rather than requiring the full app.main.app.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import get_db
from app.routers import search as search_router
from app.services.auth.dependencies import get_current_user


def _make_app(monkeypatch):
    app = FastAPI()
    app.include_router(search_router.router)

    fake_user = SimpleNamespace(id=uuid.uuid4(), role="user", department="engineering", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: iter([None])

    fake_decision = SimpleNamespace(role="user", knowledge_departments=("engineering",), max_concurrent_requests=None)
    monkeypatch.setattr(search_router, "authorize_llm_request", lambda *a, **k: fake_decision)
    monkeypatch.setattr(search_router, "search_with_reranking", lambda *a, **k: ([], True))
    monkeypatch.setattr(search_router, "record_search", lambda *a, **k: None)

    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    return _make_app(monkeypatch)


def test_valid_request_succeeds(client):
    response = client.post("/search", json={"query": "hello"})
    assert response.status_code == 200


def test_oversized_query_is_rejected(client):
    response = client.post("/search", json={"query": "x" * 2001})
    assert response.status_code == 422


def test_empty_query_is_rejected(client):
    response = client.post("/search", json={"query": ""})
    assert response.status_code == 422


def test_top_k_above_ceiling_is_rejected(client):
    response = client.post("/search", json={"query": "q", "top_k": 201})
    assert response.status_code == 422


def test_top_k_below_one_is_rejected(client):
    response = client.post("/search", json={"query": "q", "top_k": 0})
    assert response.status_code == 422


def test_top_k_at_ceiling_is_accepted(client):
    response = client.post("/search", json={"query": "q", "top_k": 200})
    assert response.status_code == 200


def test_too_many_document_ids_is_rejected(client):
    response = client.post("/search", json={"query": "q", "filters": {"document_ids": [str(uuid.uuid4()) for _ in range(201)]}})
    assert response.status_code == 422


def test_oversized_action_is_rejected(client):
    response = client.post("/search", json={"query": "q", "action": "x" * 65})
    assert response.status_code == 422
