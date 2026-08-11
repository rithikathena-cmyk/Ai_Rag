import logging
import time
import uuid
from dataclasses import dataclass

from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.qdrant import get_qdrant_client
from app.db.resilience import POSTGRES_EXCEPTIONS, QDRANT_EXCEPTIONS, postgres_call_with_retry, qdrant_call_with_retry
from app.models.chunk import ChunkModel
from app.services.embedding.qdrant_store import ensure_collection
from app.services.guardrails.pii import DualText
from app.services.monitoring.metrics import record_retrieval_error
from app.services.retrieval.metadata_filter import build_qdrant_filter, resolve_document_ids
from app.services.retrieval.query_vectors import embed_query, sparse_query_vector

logger = logging.getLogger(__name__)

_SEARCH_UNAVAILABLE = "Search is temporarily unavailable. Please try again shortly."


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    parent_chunk_id: uuid.UUID | None
    text: str  # original, authorized chunk content — RBAC has already run by the time a hit exists (see hybrid_search)
    strategy: str
    score: float
    # PII-redacted counterpart of `text`, populated by
    # search_with_reranking() (after reranking — the cross-encoder needs
    # real text to score relevance). Empty until then; every caller outside
    # this module reaches a SearchHit only through search_with_reranking(),
    # so it's always populated by the time anything downstream sees it.
    # `text` itself is never overwritten/redacted — see
    # services/guardrails/pii.py::DualText for why the two stay separate.
    display_text: str = ""


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
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    knowledge_departments: tuple[str, ...] | None = None,
    allow_unfiltered: bool = False,
    request_id: str | None = None,
) -> tuple[list[SearchHit], dict[str, float]]:
    # Stage timings are returned alongside the hits (not just logged) so
    # callers — currently search_with_reranking — can fold them into one
    # per-query metrics record for the admin dashboard, keyed the same way
    # this was manually profiled: filter/embed/sparse/qdrant/rerank.
    stages: dict[str, float] = {}

    def _timed(key: str, fn):
        start = time.perf_counter()
        result = fn()
        stages[key] = (time.perf_counter() - start) * 1000
        return result

    def _run_postgres(key: str, fn):
        def wrapped():
            try:
                return postgres_call_with_retry(fn, agent_name=f"search.{key}", db=db)
            except POSTGRES_EXCEPTIONS as exc:
                logger.exception("search: database query failed (stage=%s, request_id=%s)", key, request_id)
                record_retrieval_error(key, type(exc).__name__, request_id=request_id)
                raise AppError(503, "search_unavailable", _SEARCH_UNAVAILABLE) from exc

        return _timed(key, wrapped)

    def _run_qdrant(key: str, fn):
        def wrapped():
            try:
                return qdrant_call_with_retry(fn, agent_name=f"search.{key}")
            except QDRANT_EXCEPTIONS as exc:
                logger.exception("search: qdrant call failed (stage=%s, request_id=%s)", key, request_id)
                record_retrieval_error(key, type(exc).__name__, request_id=request_id)
                raise AppError(503, "search_unavailable", _SEARCH_UNAVAILABLE) from exc

        return _timed(key, wrapped)

    # Metadata Filter stage: resolved against Postgres (the metadata source of
    # truth) up front, then pushed down into the dense/sparse Qdrant queries
    # below instead of applied as a post-filter — a post-filter risks starving
    # top_k whenever the closest vector hits belong mostly to excluded docs.
    resolved_ids = _run_postgres("filter_ms", lambda: resolve_document_ids(
        db,
        document_id=document_id,
        document_ids=document_ids,
        document_type=document_type,
        classification=classification,
        language=language,
        latest_version_only=latest_version_only,
        user_id=user_id,
        role=role,
        knowledge_departments=knowledge_departments,
        allow_unfiltered=allow_unfiltered,
    ))
    if resolved_ids is not None and len(resolved_ids) == 0:
        return [], stages
    qdrant_filter = build_qdrant_filter(resolved_ids)

    _run_qdrant("ensure_collection_ms", ensure_collection)
    client = get_qdrant_client()
    collection = settings.qdrant_collection_name

    dense_vector = _timed("embed_ms", lambda: embed_query(query)) if mode in ("hybrid", "semantic") else None
    sparse_terms = _run_postgres("sparse_ms", lambda: sparse_query_vector(db, query)) if mode in ("hybrid", "keyword") else {}

    if mode == "keyword":
        if not sparse_terms:
            return [], stages
        sparse_vector = SparseVector(indices=list(sparse_terms.keys()), values=list(sparse_terms.values()))
        response = _run_qdrant("qdrant_ms", lambda: client.query_points(
            collection_name=collection, query=sparse_vector, using=settings.qdrant_sparse_vector_name,
            query_filter=qdrant_filter, limit=top_k, with_payload=True,
        ))
        return [_to_hit(p) for p in response.points], stages

    if mode == "semantic":
        response = _run_qdrant("qdrant_ms", lambda: client.query_points(
            collection_name=collection, query=dense_vector, using=settings.qdrant_dense_vector_name,
            query_filter=qdrant_filter, limit=top_k, with_payload=True,
        ))
        return [_to_hit(p) for p in response.points], stages

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
        response = _run_qdrant("qdrant_ms", lambda: client.query_points(
            collection_name=collection, query=dense_vector, using=settings.qdrant_dense_vector_name,
            query_filter=qdrant_filter, limit=top_k, with_payload=True,
        ))
        return [_to_hit(p) for p in response.points], stages

    response = _run_qdrant("qdrant_ms", lambda: client.query_points(
        collection_name=collection, prefetch=prefetch, query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k, with_payload=True,
    ))
    return [_to_hit(p) for p in response.points], stages


def fetch_parent_context(
    db: Session, hits: list[SearchHit], *, max_expansions: int, max_chars: int
) -> dict[uuid.UUID, DualText]:
    """Phase 3A — parent-child retrieval (docs/RAG_RETRIEVAL.md). For hits
    whose `parent_chunk_id` is set, returns {chunk_id: DualText(raw, display)}
    for at most `max_expansions` hits, highest-scoring first (`hits` is
    expected to already be reranked/sorted). Returns DualText rather than a
    plain string for the same reason SearchHit carries both `text` and
    `display_text` — parent-chunk content is a separate Postgres read from
    the hits search_with_reranking already dual-represents, so it needs its
    own raw/display split; callers (services/agents/retrieval_agent.py) pick
    .raw for the LLM-facing payload and .display for anything persisted or
    returned to the user.

    Authorization: a parent chunk always belongs to the *same document* as
    its child (services/chunking/parent_child.py builds both from the same
    parsed document in one pass — a child's `parent_index` only ever points
    at a parent chunk index/document produced in that same call). Every hit
    passed in here already survived resolve_document_ids()'s permission/
    department allowlist *before* it ever reached Qdrant (see hybrid_search
    above) — fetching a parent by ID from that same document doesn't grant
    access to anything new, it only reads more text from a document this
    caller was already authorized to retrieve from. No separate permission
    check is performed here because none is needed; this function must only
    ever be called with hits that already passed that filter.

    Deduplication: a parent is fetched at most once and attached only to the
    first (highest-scoring) hit that references it — a second hit sharing
    the same parent gets no parent_context, so identical parent text is
    never duplicated across multiple hits/citations in one result set.
    """
    if max_expansions <= 0:
        return {}

    chunk_to_parent: dict[uuid.UUID, uuid.UUID] = {}
    seen_parents: set[uuid.UUID] = set()
    for hit in hits:
        if hit.parent_chunk_id is None or hit.parent_chunk_id in seen_parents:
            continue
        if len(chunk_to_parent) >= max_expansions:
            break
        seen_parents.add(hit.parent_chunk_id)
        chunk_to_parent[hit.chunk_id] = hit.parent_chunk_id

    if not chunk_to_parent:
        return {}

    parent_ids = list(seen_parents)
    parent_text_by_id = {
        row[0]: row[1]
        for row in db.query(ChunkModel.id, ChunkModel.text).filter(ChunkModel.id.in_(parent_ids)).all()
    }

    result: dict[uuid.UUID, DualText] = {}
    for chunk_id, parent_id in chunk_to_parent.items():
        parent_text = parent_text_by_id.get(parent_id)
        if parent_text is None:
            continue  # parent row missing (e.g. deleted) — child's own text still stands on its own
        truncated = parent_text if len(parent_text) <= max_chars else parent_text[:max_chars].rstrip() + "…"
        result[chunk_id] = DualText.from_raw(truncated)
    return result
