"""services/ingestion/consistency.py — detects drift between Postgres
(ingestion's source of truth for "this document exists") and Qdrant
(retrieval's source of truth for "this document is actually searchable").
Written after tracing a real production bug: WM_1.pdf had
DocumentModel.status == "completed" and 8 real ChunkModel rows, each with a
qdrant_point_id already populated, but zero actual points in Qdrant — the
Qdrant collection drifted independently of Postgres (Qdrant runs natively on
the host per docker-compose.yml's own comment, decoupled from Postgres'
container lifecycle) sometime after a genuinely successful ingestion. No
code path in routers/documents.py could have caught this: its try/except
around upsert_chunks() only ever sees an exception raised *during that
request*, and none was raised — the upsert really did succeed at the time.

Stubs the DB/Qdrant boundary rather than requiring a live stack, matching
this suite's established convention.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.services.ingestion import consistency


def _doc(filename="f.pdf"):
    return SimpleNamespace(filename=filename)


def _db(documents: dict, chunk_counts: dict):
    """documents: {document_id: fake_doc}; chunk_counts: {document_id: int}.
    Supports exactly what check_document()/check_all_documents() call:
    db.get(Model, id), db.query(ChunkModel).filter(...).count(), and
    db.query(DocumentModel.id).join(...).distinct().all() — one flexible
    query-node handles both shapes since check_document() only ever calls
    .filter()/.count() and check_all_documents() only ever calls
    .join()/.distinct()/.all()."""

    class _QueryNode:
        def __init__(self, db):
            self._db = db

        def filter(self, *a, **k):
            return self

        def count(self):
            return chunk_counts[self._db._current]

        def join(self, *a, **k):
            return self

        def distinct(self):
            return self

        def all(self):
            return [(i,) for i in documents]

    class _Db:
        _current = None

        def get(self, model, document_id):
            return documents.get(document_id)

        def query(self, *args, **kwargs):
            return _QueryNode(self)

    return _Db()


@pytest.fixture(autouse=True)
def _no_real_qdrant(monkeypatch):
    # Every test below monkeypatches document_point_count explicitly; this
    # guard just ensures a test that forgets to fails loudly instead of
    # silently hitting a real (possibly absent) Qdrant instance.
    def _unexpected(document_id):
        raise AssertionError("document_point_count must be monkeypatched in every test")

    monkeypatch.setattr(consistency, "document_point_count", _unexpected)


def test_consistent_document_reports_matching_counts(monkeypatch):
    doc_id = uuid.uuid4()
    db = _db({doc_id: _doc("handbook.pdf")}, {doc_id: 8})
    db._current = doc_id
    monkeypatch.setattr(consistency, "document_point_count", lambda did: 8)

    report = consistency.check_document(db, doc_id)

    assert report.consistent is True
    assert report.postgres_chunk_count == 8
    assert report.qdrant_point_count == 8
    assert report.filename == "handbook.pdf"


def test_inconsistent_document_is_flagged(monkeypatch):
    """The exact WM_1.pdf shape: 8 Postgres chunk rows, 0 Qdrant points.
    Status isn't even consulted here — the point-count mismatch alone is the
    signal, independent of what Postgres *believes* happened."""
    doc_id = uuid.uuid4()
    db = _db({doc_id: _doc("WM_1.pdf")}, {doc_id: 8})
    db._current = doc_id
    monkeypatch.setattr(consistency, "document_point_count", lambda did: 0)

    report = consistency.check_document(db, doc_id)

    assert report.consistent is False
    assert report.postgres_chunk_count == 8
    assert report.qdrant_point_count == 0


def test_unknown_document_raises():
    db = _db({}, {})

    with pytest.raises(ValueError):
        consistency.check_document(db, uuid.uuid4())


def test_check_all_documents_only_flags_the_mismatched_one(monkeypatch):
    consistent_id, inconsistent_id = uuid.uuid4(), uuid.uuid4()
    documents = {consistent_id: _doc("a.txt"), inconsistent_id: _doc("WM_1.pdf")}
    chunk_counts = {consistent_id: 2, inconsistent_id: 8}
    point_counts = {consistent_id: 2, inconsistent_id: 0}
    db = _db(documents, chunk_counts)

    def _fake_check_document(db_arg, document_id):
        return consistency.ConsistencyReport(
            document_id=document_id, filename=documents[document_id].filename,
            postgres_chunk_count=chunk_counts[document_id], qdrant_point_count=point_counts[document_id],
        )

    monkeypatch.setattr(consistency, "check_document", _fake_check_document)

    reports = consistency.check_all_documents(db)

    assert len(reports) == 2
    inconsistent = [r for r in reports if not r.consistent]
    assert len(inconsistent) == 1
    assert inconsistent[0].filename == "WM_1.pdf"
