"""routers/documents.py::reindex_document — the existing, already-built
maintenance mechanism for exactly the class of bug
tests/ingestion/test_consistency.py documents (Postgres says a document is
fine, Qdrant doesn't actually have its points): "recomputes embeddings...
and re-upserts them into Qdrant" for a document's existing chunk rows.
Previously untested at the behavioral level (tests/test_documents_rbac.py
only checks route wiring/permission gates structurally).

Also covers the Postgres/Qdrant ID-mapping and collection-config invariants
that make reindexing (and drift-detection) meaningful in the first place —
see services/chunking/persistence.py::build_chunk_rows and
services/embedding/qdrant_store.py.

Uses real DocumentModel/ChunkModel instances (never persisted — SQLAlchemy
models are plain Python objects until added to a session) rather than
SimpleNamespace stand-ins, so this exercises the router's actual
_to_response() field access without hand-maintaining a parallel field list.
No live Postgres/Qdrant/network anywhere in this file.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import get_db
from app.models.chunk import ChunkModel
from app.models.document import DocumentModel
from app.routers import documents as documents_router
from app.services.auth.dependencies import get_current_user
from app.services.chunking.persistence import build_chunk_rows
from app.services.chunking.types import Chunk


def _document(document_id=None) -> DocumentModel:
    return DocumentModel(
        id=document_id or uuid.uuid4(), filename="WM_1.pdf", file_extension=".pdf", document_type="pdf",
        file_size_bytes=1024, status="completed", storage_dir="/tmp/doc", chunk_count=2,
        lineage_id=uuid.uuid4(), version_number=1, is_latest_version=True,
        table_count=0, image_count=0,
        created_at=datetime.now(timezone.utc),
    )


def _chunk(document_id, index, text="chunk text") -> ChunkModel:
    return ChunkModel(
        id=uuid.uuid4(), document_id=document_id, chunk_index=index, text=text,
        token_count=10, strategy="recursive", qdrant_point_id=str(uuid.uuid4()), embedding_model="BAAI/bge-m3",
        created_at=datetime.now(timezone.utc),
    )


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._rows

    def delete(self, *a, **k):
        return 0

    def in_(self, *a, **k):  # unused directly, kept for parity with real query chains
        return self


class _FakeDb:
    def __init__(self, document, chunk_rows):
        self._document = document
        self._chunk_rows = chunk_rows
        self.added = []
        self.committed = 0

    def get(self, model, document_id):
        return self._document if self._document.id == document_id else None

    def query(self, *a, **k):
        return _FakeQuery(self._chunk_rows)

    def add_all(self, rows):
        self.added.extend(rows)

    def commit(self):
        self.committed += 1

    def refresh(self, obj):
        pass


def _make_app(monkeypatch, document, chunk_rows, *, upsert_side_effect=None):
    app = FastAPI()
    app.include_router(documents_router.router)

    admin = SimpleNamespace(id=uuid.uuid4(), role="admin", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: admin

    db = _FakeDb(document, chunk_rows)

    def _fake_get_db():
        yield db

    app.dependency_overrides[get_db] = _fake_get_db

    monkeypatch.setattr(documents_router, "embed_texts", lambda texts: [[0.1] * 4 for _ in texts])
    monkeypatch.setattr(documents_router, "compute_term_frequencies", lambda texts: {})
    monkeypatch.setattr(documents_router, "build_sparse_index", lambda db, chunk_rows, term_freqs: ([], []))

    captured = {}
    if upsert_side_effect is not None:
        def _upsert(rows, vectors, sparse_vectors):
            raise upsert_side_effect
    else:
        def _upsert(rows, vectors, sparse_vectors):
            captured["rows"] = rows
            captured["vectors"] = vectors

    monkeypatch.setattr(documents_router, "upsert_chunks", _upsert)

    return TestClient(app), db, captured


# --------------------------------------------------- reindex success path

def test_reindex_restores_missing_points_and_reports_completed(monkeypatch):
    doc = _document()
    chunks = [_chunk(doc.id, 0), _chunk(doc.id, 1)]
    client, db, captured = _make_app(monkeypatch, doc, chunks)

    response = client.post(f"/documents/{doc.id}/reindex")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert doc.status == "completed"
    assert doc.error_message is None
    # The exact chunk rows already in Postgres were re-upserted — same
    # objects, same ids — not a fresh set with new ids.
    assert captured["rows"] == chunks


def test_reindex_upsert_failure_marks_document_degraded(monkeypatch):
    """Proves the existing try/except (routers/documents.py's
    reindex_document) actually does what its code implies: a real Qdrant
    failure during reindex is caught and reflected in status/error_message,
    not silently swallowed the way the original WM_1.pdf drift was invisible
    (that drift happened *after* a request succeeded — this is the case
    where the request-time upsert itself genuinely fails)."""
    doc = _document()
    chunks = [_chunk(doc.id, 0)]
    client, db, _captured = _make_app(
        monkeypatch, doc, chunks, upsert_side_effect=ConnectionError("Qdrant unavailable"),
    )

    response = client.post(f"/documents/{doc.id}/reindex")

    assert response.status_code == 200  # the route itself still returns a response...
    body = response.json()
    assert body["status"] == "degraded"  # ...but honestly reports the failure
    assert "qdrant upsert failed" in body["error_message"]
    assert doc.status == "degraded"


def test_reindex_nonexistent_document_is_404(monkeypatch):
    doc = _document()
    client, _db, _captured = _make_app(monkeypatch, doc, [])

    response = client.post(f"/documents/{uuid.uuid4()}/reindex")

    assert response.status_code == 404


def test_reindex_document_with_no_chunks_is_rejected(monkeypatch):
    doc = _document()
    client, _db, _captured = _make_app(monkeypatch, doc, [])

    response = client.post(f"/documents/{doc.id}/reindex")

    assert response.status_code == 422


# --------------------------------------------------- Postgres/Qdrant ID mapping

def test_build_chunk_rows_assigns_the_same_id_as_qdrant_point_id():
    """The invariant document_point_count()/upsert_chunks() rely on: a
    ChunkModel row's Postgres primary key IS the Qdrant point id (as a
    string) — not two independently-generated identifiers that could drift
    apart."""
    document_id = uuid.uuid4()
    chunks = [Chunk(index=0, text="a", token_count=1, strategy="recursive"), Chunk(index=1, text="b", token_count=1, strategy="recursive")]

    rows = build_chunk_rows(document_id, chunks, "BAAI/bge-m3")

    for row in rows:
        assert row.qdrant_point_id == str(row.id)


def test_upsert_chunks_point_id_matches_chunk_row_id(monkeypatch):
    """services/embedding/qdrant_store.py::upsert_chunks builds each Qdrant
    PointStruct.id from row.id directly — confirms the write side uses the
    same id build_chunk_rows() already committed to Postgres, so a
    consistency check (or a reindex) can always correlate the two stores by
    a single id, never a separate mapping table."""
    from app.services.embedding import qdrant_store

    captured = {}

    class _FakeClient:
        def upsert(self, collection_name, points):
            captured["points"] = points

    monkeypatch.setattr(qdrant_store, "ensure_collection", lambda: None)
    monkeypatch.setattr(qdrant_store, "get_qdrant_client", lambda: _FakeClient())

    document_id = uuid.uuid4()
    chunks = [Chunk(index=0, text="a", token_count=1, strategy="recursive")]
    rows = build_chunk_rows(document_id, chunks, "BAAI/bge-m3")

    qdrant_store.upsert_chunks(rows, [[0.1, 0.2]])

    assert captured["points"][0].id == str(rows[0].id)


def test_reindexing_twice_reuses_the_same_point_ids_no_duplication(monkeypatch):
    """Reindexing must never create new chunk/point ids for existing
    content — Qdrant's upsert-by-id semantics mean the same id replaces the
    prior point rather than duplicating it, but only if this codebase
    consistently reuses the same id across repeated reindex calls, which
    this proves at the code level (Qdrant's own storage semantics aren't
    re-tested here — that's Qdrant's job, not this codebase's)."""
    from app.services.embedding import qdrant_store

    upserted_point_ids = []

    class _FakeClient:
        def upsert(self, collection_name, points):
            upserted_point_ids.append([p.id for p in points])

    monkeypatch.setattr(qdrant_store, "ensure_collection", lambda: None)
    monkeypatch.setattr(qdrant_store, "get_qdrant_client", lambda: _FakeClient())

    document_id = uuid.uuid4()
    existing_chunk = _chunk(document_id, 0)  # id already assigned, as if read back from Postgres

    qdrant_store.upsert_chunks([existing_chunk], [[0.1, 0.2]])
    qdrant_store.upsert_chunks([existing_chunk], [[0.1, 0.2]])  # a second reindex of the same chunk

    assert upserted_point_ids[0] == upserted_point_ids[1] == [str(existing_chunk.id)]


# --------------------------------------------------- collection config identical between ingestion and retrieval

def test_ingestion_and_retrieval_ensure_collection_share_one_implementation():
    """services/retrieval/search.py imports ensure_collection from
    services/embedding/qdrant_store — the same function upsert_chunks()
    calls — rather than each maintaining its own collection-creation logic
    that could drift apart (different vector names, different distance
    metric, etc.)."""
    from app.services.embedding.qdrant_store import ensure_collection as ingestion_ensure_collection
    from app.services.retrieval.search import ensure_collection as retrieval_ensure_collection

    assert ingestion_ensure_collection is retrieval_ensure_collection
