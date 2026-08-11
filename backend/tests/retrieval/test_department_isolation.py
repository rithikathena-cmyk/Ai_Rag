"""End-to-end regression test for the "authorize before retrieval, LLM never
decides access" guarantee (enterprise permission model spec, secure RAG
flow): a role's chat/search query must never even reach Qdrant with an
out-of-department document in scope, let alone surface its chunks.

The pure policy rule (apply_category_policy) is already thoroughly unit
tested in tests/llm_rbac/test_category_policy.py, and the guard against an
unfiltered call is tested in tests/retrieval/test_metadata_filter_guard.py —
this test instead proves the real *wiring*: hybrid_search's role/
knowledge_departments params flow through resolve_document_ids ->
filter_by_category -> build_qdrant_filter and actually constrain the
query_filter handed to Qdrant, using the real (non-mocked)
resolve_document_ids/filter_by_category functions with only the DB query
boundary stubbed, matching this suite's established convention.
"""

import uuid

import pytest

from app.services.retrieval import search


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    """Documents are (id, department, access_roles) tuples. Returns the
    right projection shape depending on how many columns resolve_document_ids/
    filter_by_category asked for — either just `.id` (the base
    latest-version query) or `.id, .department, .access_roles` (the category
    lookup) — mirroring the exact two query shapes those functions issue."""

    def __init__(self, documents: list[tuple[uuid.UUID, str, list | None]]):
        self._documents = documents

    def query(self, *entities):
        if len(entities) == 1:
            return _FakeQuery([(doc_id,) for doc_id, _, _ in self._documents])
        return _FakeQuery(list(self._documents))


def _fake_qdrant_client(captured: dict):
    class _Client:
        def query_points(self, **kwargs):
            captured["query_filter"] = kwargs.get("query_filter")

            class _Response:
                points = []

            return _Response()

    return _Client()


@pytest.fixture(autouse=True)
def _stub_infra(monkeypatch):
    monkeypatch.setattr(search, "ensure_collection", lambda: None)
    monkeypatch.setattr(search, "embed_query", lambda q: [0.1, 0.2])
    # Isolates this test to department (category) filtering specifically —
    # per-user document grants are filter_by_permission's own concern,
    # already covered by tests/guardrails/test_retrieval_permissions.py.
    monkeypatch.setattr("app.services.retrieval.metadata_filter.filter_by_permission", lambda db, ids, user_id: ids)


def test_employee_search_never_includes_an_hr_department_document(monkeypatch):
    manufacturing_doc = uuid.uuid4()
    hr_doc = uuid.uuid4()
    fake_db = _FakeDb([
        (manufacturing_doc, "manufacturing", None),
        (hr_doc, "hr", None),
    ])
    captured: dict = {}
    monkeypatch.setattr(search, "get_qdrant_client", lambda: _fake_qdrant_client(captured))

    search.hybrid_search(
        fake_db, query="what is our policy", mode="semantic",
        user_id=uuid.uuid4(), role="user", knowledge_departments=("manufacturing",),
    )

    query_filter = captured["query_filter"]
    assert query_filter is not None, "resolve_document_ids narrowed to an empty set — no filter was ever built"
    allowed_ids = query_filter.must[0].match.any
    assert str(manufacturing_doc) in allowed_ids
    assert str(hr_doc) not in allowed_ids


def test_admin_with_full_knowledge_departments_sees_both(monkeypatch):
    manufacturing_doc = uuid.uuid4()
    hr_doc = uuid.uuid4()
    fake_db = _FakeDb([
        (manufacturing_doc, "manufacturing", None),
        (hr_doc, "hr", None),
    ])
    captured: dict = {}
    monkeypatch.setattr(search, "get_qdrant_client", lambda: _fake_qdrant_client(captured))

    search.hybrid_search(
        fake_db, query="enterprise overview", mode="semantic",
        user_id=uuid.uuid4(), role="admin",
        knowledge_departments=("manufacturing", "hr", "engineering", "executive"),
    )

    query_filter = captured["query_filter"]
    allowed_ids = query_filter.must[0].match.any
    assert str(manufacturing_doc) in allowed_ids
    assert str(hr_doc) in allowed_ids
