import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.postgres import get_db
from app.gateway.usage_tracker import record_denied, record_search
from app.models.document import DocumentModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.llm_rbac import policy_loader
from app.services.llm_rbac.engine import authorize_llm_request
from app.services.llm_rbac.rate_limiter import ConcurrencyGuard
from app.services.reranking.pipeline import search_with_reranking

router = APIRouter()


class SearchFilters(BaseModel):
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    document_type: str | None = Field(default=None, max_length=64)
    classification: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=32)
    latest_version_only: bool = True


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    # Hard defense-in-depth ceiling, independent of (and in addition to) the
    # settings.search_max_top_k router-level clamp below — a blatantly
    # abusive top_k gets a 422 instead of being silently clamped.
    top_k: int = Field(default=10, ge=1, le=200)
    rerank: bool = True
    filters: SearchFilters = Field(default_factory=SearchFilters)
    # Optional capability name matching llm_rbac.yaml's permission catalog —
    # see routers/chat.py's ChatRequest.action for the full rationale.
    # max_length matches GatewayUsageLogModel.requested_capability's column
    # width (String(64)) to avoid silent truncation surprises.
    action: str | None = Field(default=None, max_length=64)


class SearchResultItem(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str | None
    chunk_index: int
    parent_chunk_id: uuid.UUID | None
    text: str
    strategy: str
    score: float


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    reranked: bool
    results: list[SearchResultItem]


@router.post("/search", response_model=SearchResponse)
def search(
    request: Request, body: SearchRequest, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)
):
    request_id = getattr(request.state, "request_id", None)

    # Looked up independently of authorize_llm_request() below so the
    # concurrency guard can wrap that call too, not just the retrieval that
    # follows it — max_concurrent_requests is otherwise only known as part of
    # authorize_llm_request()'s own return value, which is too late: under a
    # concurrent burst from one user, authorize_llm_request()'s own Postgres
    # quota check (role_usage_counters, one row per user/period) is exactly
    # what gets hammered, and a guard placed only around the retrieval call
    # never throttles that. Mirrors engine.py's own kill-switch handling so
    # this lookup agrees with what authorize_llm_request() will actually do.
    role_cfg = policy_loader.role_config(current_user.role)
    max_concurrent = role_cfg.quotas.get("max_concurrent_requests") if policy_loader.is_enabled() else None

    top_k = max(1, min(body.top_k, settings.search_max_top_k))
    start = time.perf_counter()

    with ConcurrencyGuard(current_user.id, max_concurrent):
        try:
            decision = authorize_llm_request(db, current_user, endpoint="search", action=body.action)
        except AppError as exc:
            record_denied(
                agent_name="search_endpoint", user_id=current_user.id, role=current_user.role,
                department=current_user.department, denial_reason=str(exc.detail),
                requested_capability=body.action,
            )
            raise

        hits, reranked = search_with_reranking(
            db,
            query=body.query,
            mode=body.mode,
            top_k=top_k,
            use_reranker=body.rerank,
            document_id=body.filters.document_id,
            document_ids=body.filters.document_ids,
            document_type=body.filters.document_type,
            classification=body.filters.classification,
            language=body.filters.language,
            latest_version_only=body.filters.latest_version_only,
            user_id=current_user.id,
            role=decision.role,
            knowledge_departments=decision.knowledge_departments,
            request_id=request_id,
        )
    latency_ms = (time.perf_counter() - start) * 1000

    filenames: dict[uuid.UUID, str] = {}
    if hits:
        doc_ids = {h.document_id for h in hits}
        rows = db.query(DocumentModel.id, DocumentModel.filename).filter(DocumentModel.id.in_(doc_ids)).all()
        filenames = {r[0]: r[1] for r in rows}

    record_search(
        request_id=request_id or str(uuid.uuid4()),
        user_id=current_user.id,
        role=decision.role,
        department=current_user.department,
        latency_ms=latency_ms,
        requested_capability=body.action,
        documents_retrieved=[str(h.document_id) for h in hits] or None,
    )

    return SearchResponse(
        query=body.query,
        mode=body.mode,
        total=len(hits),
        reranked=reranked,
        results=[
            SearchResultItem(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                document_filename=filenames.get(h.document_id),
                chunk_index=h.chunk_index,
                parent_chunk_id=h.parent_chunk_id,
                # display_text, not text: this endpoint has no LLM/agent in
                # the loop — every result here is returned straight to the
                # user, so there is no "authorized reasoning context" that
                # would justify the raw representation (see
                # services/guardrails/pii.py::DualText).
                text=h.display_text,
                strategy=h.strategy,
                score=h.score,
            )
            for h in hits
        ],
    )
