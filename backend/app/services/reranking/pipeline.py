import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.reranking.reranker import rerank
from app.services.retrieval.search import SearchHit, hybrid_search


def search_with_reranking(
    db: Session,
    *,
    query: str,
    mode: str = "hybrid",
    top_k: int = 10,
    use_reranker: bool = True,
    document_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    document_type: str | None = None,
    classification: str | None = None,
    language: str | None = None,
    latest_version_only: bool = True,
) -> tuple[list[SearchHit], bool]:
    # Retrieval pulls a wide candidate pool (settings.reranker_candidate_pool,
    # e.g. top 50) when reranking is on, so the cross-encoder has enough
    # recall to work with; without it we'd only ever be able to reorder the
    # same top_k the caller asked for.
    candidate_k = max(top_k, settings.reranker_candidate_pool) if use_reranker else top_k

    hits = hybrid_search(
        db,
        query=query,
        mode=mode,
        top_k=candidate_k,
        document_id=document_id,
        document_ids=document_ids,
        document_type=document_type,
        classification=classification,
        language=language,
        latest_version_only=latest_version_only,
    )

    reranked = False
    if use_reranker and hits:
        hits = rerank(query, hits)
        reranked = True

    return hits[:top_k], reranked
