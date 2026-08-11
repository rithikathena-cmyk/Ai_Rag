import dataclasses
import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.guardrails.pii import DualText
from app.services.monitoring.metrics import record_retrieval_error, record_retrieval_metrics
from app.services.reranking.reranker import rerank
from app.services.retrieval.search import SearchHit, hybrid_search

logger = logging.getLogger(__name__)


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
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    knowledge_departments: tuple[str, ...] | None = None,
    allow_unfiltered: bool = False,
    request_id: str | None = None,
) -> tuple[list[SearchHit], bool]:
    # Retrieval pulls a wide candidate pool (settings.reranker_candidate_pool,
    # e.g. top 20) when reranking is on, so the cross-encoder has enough
    # recall to work with; without it we'd only ever be able to reorder the
    # same top_k the caller asked for.
    candidate_k = max(top_k, settings.reranker_candidate_pool) if use_reranker else top_k

    hits, stages = hybrid_search(
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
        user_id=user_id,
        role=role,
        knowledge_departments=knowledge_departments,
        allow_unfiltered=allow_unfiltered,
        request_id=request_id,
    )
    candidate_count = len(hits)

    # Reranking is an enhancement, not load-bearing — a cross-encoder failure
    # degrades to serving hybrid_search's own ranking (reranked=False) rather
    # than failing the whole search.
    reranked = False
    if use_reranker and hits:
        rerank_start = time.perf_counter()
        try:
            hits = rerank(query, hits)
            reranked = True
        except Exception as exc:
            logger.exception("search: reranking failed, serving unranked results (request_id=%s)", request_id)
            record_retrieval_error("rerank", type(exc).__name__, request_id=request_id)
        stages["rerank_ms"] = (time.perf_counter() - rerank_start) * 1000

    stages["total_ms"] = sum(stages.values())
    hits = hits[:top_k]
    record_retrieval_metrics(query, stages, candidate_count=candidate_count, result_count=len(hits), request_id=request_id)

    # PII dual-representation boundary — this is the one function both
    # external-facing consumers of retrieved chunk text share
    # (routers/search.py's direct /search response, and
    # services/agents/retrieval_agent.py's chat/planner tool result). Every
    # hit gets BOTH representations from here on: `text` keeps the original,
    # authorized content (for an authorized LLM/agent tool context only —
    # see services/agents/planner.py's LLM-payload/public-view split), and
    # `display_text` is `redact_pii(text)` — the only representation allowed
    # into anything persisted or returned to a user (routers/search.py,
    # citations, chat history). Neither call site needs its own redaction
    # pass; they only need to pick the right field. Reuses
    # services/guardrails/pii.py's DualText/redact_pii — no second detector.
    #
    # Computed after rerank() rather than before: the cross-encoder needs
    # real chunk text to score relevance accurately (see rerank() above) — a
    # query about a phone number would score terribly against a hit whose
    # text had already been replaced with "[REDACTED_PHONE]". Builds fresh
    # SearchHit objects (dataclasses.replace) rather than mutating in place —
    # `text` on the object hybrid_search/rerank produced is left exactly as
    # it was; only the returned copy gains a populated display_text.
    hits = [dataclasses.replace(h, display_text=DualText.from_raw(h.text).display) for h in hits]

    return hits, reranked
