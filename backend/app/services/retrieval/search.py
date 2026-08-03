import uuid
from dataclasses import dataclass

from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.qdrant import get_qdrant_client
from app.services.embedding.qdrant_store import ensure_collection
from app.services.retrieval.metadata_filter import build_qdrant_filter, resolve_document_ids
from app.services.retrieval.query_vectors import embed_query, sparse_query_vector


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    parent_chunk_id: uuid.UUID | None
    text: str
    strategy: str
    score: float


def _to_hit(point) -> SearchHit:
    payload = point.payload or {}
    parent = payload.get("parent_chunk_id")
    return SearchHit(
        chunk_id=uuid.UUID(payload["chunk_id"]),
        document_id=uuid.UUID(payload["document_id"]),
        chunk_index=payload.get("chunk_index", 0),
        parent_chunk_id=uuid.UUID(parent) if parent else None,
        text=payload.get("text", ""),
        strategy=payload.get("strategy", ""),
        score=point.score,
    )


def hybrid_search(
    db: Session,
    *,
    query: str,
    mode: str = "hybrid",
    top_k: int = 10,
    document_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    document_type: str | None = None,
    classification: str | None = None,
    language: str | None = None,
    latest_version_only: bool = True,
) -> list[SearchHit]:
    # Metadata Filter stage: resolved against Postgres (the metadata source of
    # truth) up front, then pushed down into the dense/sparse Qdrant queries
    # below instead of applied as a post-filter — a post-filter risks starving
    # top_k whenever the closest vector hits belong mostly to excluded docs.
    resolved_ids = resolve_document_ids(
        db,
        document_id=document_id,
        document_ids=document_ids,
        document_type=document_type,
        classification=classification,
        language=language,
        latest_version_only=latest_version_only,
    )
    if resolved_ids is not None and len(resolved_ids) == 0:
        return []
    qdrant_filter = build_qdrant_filter(resolved_ids)

    ensure_collection()
    client = get_qdrant_client()
    collection = settings.qdrant_collection_name

    dense_vector = embed_query(query) if mode in ("hybrid", "semantic") else None
    sparse_terms = sparse_query_vector(db, query) if mode in ("hybrid", "keyword") else {}

    if mode == "keyword":
        if not sparse_terms:
            return []
        sparse_vector = SparseVector(indices=list(sparse_terms.keys()), values=list(sparse_terms.values()))
        response = client.query_points(
            collection_name=collection, query=sparse_vector, using=settings.qdrant_sparse_vector_name,
            query_filter=qdrant_filter, limit=top_k, with_payload=True,
        )
        return [_to_hit(p) for p in response.points]

    if mode == "semantic":
        response = client.query_points(
            collection_name=collection, query=dense_vector, using=settings.qdrant_dense_vector_name,
            query_filter=qdrant_filter, limit=top_k, with_payload=True,
        )
        return [_to_hit(p) for p in response.points]

    # hybrid: dense + sparse candidate pools merged via Qdrant's native RRF fusion
    prefetch = [
        Prefetch(
            query=dense_vector, using=settings.qdrant_dense_vector_name,
            filter=qdrant_filter, limit=settings.hybrid_prefetch_limit,
        )
    ]
    if sparse_terms:
        sparse_vector = SparseVector(indices=list(sparse_terms.keys()), values=list(sparse_terms.values()))
        prefetch.append(
            Prefetch(
                query=sparse_vector, using=settings.qdrant_sparse_vector_name,
                filter=qdrant_filter, limit=settings.hybrid_prefetch_limit,
            )
        )

    if len(prefetch) == 1:
        # No recognized query terms in the sparse vocabulary — fusion needs at
        # least two ranked lists to merge, so fall back to dense-only ranking.
        response = client.query_points(
            collection_name=collection, query=dense_vector, using=settings.qdrant_dense_vector_name,
            query_filter=qdrant_filter, limit=top_k, with_payload=True,
        )
        return [_to_hit(p) for p in response.points]

    response = client.query_points(
        collection_name=collection, prefetch=prefetch, query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k, with_payload=True,
    )
    return [_to_hit(p) for p in response.points]
