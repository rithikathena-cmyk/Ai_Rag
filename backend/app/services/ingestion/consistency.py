"""Detects drift between Postgres (source of truth for "this document was
ingested") and Qdrant (source of truth for "this document is actually
searchable"). A document can carry DocumentModel.status == "completed" —
meaning ingestion itself ran without error — while having zero points in
Qdrant, because the two stores have independent lifecycles: Qdrant runs
natively on the host (see docker-compose.yml's comment on QDRANT_HOST),
decoupled from Postgres' container lifecycle, so a Qdrant collection
reset/recreation after a document was ingested leaves no trace in Postgres
at all. Nothing in the ingestion path itself can catch this after the
fact — routers/documents.py's upload_document/reindex_document only mark
status="degraded" for an upsert that raises an exception *during that
request*; there is currently no ongoing check that a "completed" document's
Qdrant points are still actually present. This module is that check —
read-only, for a caller (routers/admin.py's index-consistency endpoint, or a
one-off diagnostic script) to consult and then decide what to do (typically:
call routers/documents.py's existing POST /documents/{id}/reindex, which
already recomputes embeddings and re-upserts correctly).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.chunk import ChunkModel
from app.models.document import DocumentModel
from app.services.embedding.qdrant_store import document_point_count


@dataclass
class ConsistencyReport:
    document_id: uuid.UUID
    filename: str
    postgres_chunk_count: int
    qdrant_point_count: int

    @property
    def consistent(self) -> bool:
        return self.postgres_chunk_count == self.qdrant_point_count


def check_document(db: Session, document_id: uuid.UUID) -> ConsistencyReport:
    doc = db.get(DocumentModel, document_id)
    if doc is None:
        raise ValueError(f"document {document_id} not found")
    postgres_count = db.query(ChunkModel).filter(ChunkModel.document_id == document_id).count()
    qdrant_count = document_point_count(document_id)
    return ConsistencyReport(
        document_id=document_id, filename=doc.filename,
        postgres_chunk_count=postgres_count, qdrant_point_count=qdrant_count,
    )


def check_all_documents(db: Session) -> list[ConsistencyReport]:
    """Every document with at least one Postgres chunk row — not just ones
    already flagged status="degraded", since that flag only reflects
    failures observed *during* the original ingestion/reindex request and
    would never catch drift introduced afterward (the exact WM_1.pdf case
    this module was written to detect: status="completed", chunk_count=8,
    zero Qdrant points)."""
    doc_ids = [
        row[0] for row in
        db.query(DocumentModel.id).join(ChunkModel, ChunkModel.document_id == DocumentModel.id).distinct().all()
    ]
    return [check_document(db, doc_id) for doc_id in doc_ids]
