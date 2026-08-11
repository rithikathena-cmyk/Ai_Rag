import uuid

from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    Modifier,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.core.config import settings
from app.db.qdrant import get_qdrant_client

_collection_ready = False


def ensure_collection() -> None:
    global _collection_ready
    if _collection_ready:
        return
    client = get_qdrant_client()
    if not client.collection_exists(settings.qdrant_collection_name):
        client.create_collection(
            collection_name=settings.qdrant_collection_name,
            vectors_config={
                settings.qdrant_dense_vector_name: VectorParams(
                    size=settings.embedding_dimension, distance=Distance.COSINE
                ),
            },
            sparse_vectors_config={
                settings.qdrant_sparse_vector_name: SparseVectorParams(modifier=Modifier.IDF),
            },
        )
    _collection_ready = True


def upsert_chunks(
    chunk_rows: list,
    vectors: list[list[float]],
    sparse_vectors: list[dict[int, float]] | None = None,
) -> None:
    if not chunk_rows:
        return
    ensure_collection()
    sparse_vectors = sparse_vectors or [None] * len(chunk_rows)

    points = []
    for row, vector, sparse in zip(chunk_rows, vectors, sparse_vectors):
        vector_payload = {settings.qdrant_dense_vector_name: vector}
        if sparse:
            vector_payload[settings.qdrant_sparse_vector_name] = SparseVector(
                indices=list(sparse.keys()), values=list(sparse.values())
            )
        points.append(
            PointStruct(
                id=str(row.id),
                vector=vector_payload,
                payload={
                    "document_id": str(row.document_id),
                    "chunk_id": str(row.id),
                    "chunk_index": row.chunk_index,
                    "parent_chunk_id": str(row.parent_chunk_id) if row.parent_chunk_id else None,
                    "text": row.text,
                    "strategy": row.strategy,
                    "token_count": row.token_count,
                },
            )
        )
    get_qdrant_client().upsert(collection_name=settings.qdrant_collection_name, points=points)


def delete_document_points(document_id: uuid.UUID) -> None:
    ensure_collection()
    get_qdrant_client().delete(
        collection_name=settings.qdrant_collection_name,
        points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]),
    )


def document_point_count(document_id: uuid.UUID) -> int:
    """How many points this document actually has in the live Qdrant
    collection right now — the ground truth to compare against Postgres'
    ChunkModel row count for the same document (see
    services/ingestion/consistency.py). A mismatch here is invisible to the
    ingestion code path itself: build_chunk_rows() assigns qdrant_point_id
    to every ChunkModel row eagerly, before any Qdrant write is even
    attempted, so that column reflects intent, not confirmed presence — this
    function is what actually asks Qdrant."""
    ensure_collection()
    return get_qdrant_client().count(
        collection_name=settings.qdrant_collection_name,
        count_filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]),
    ).count
