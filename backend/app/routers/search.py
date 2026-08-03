import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.postgres import get_db
from app.models.document import Document
from app.services.reranking.pipeline import search_with_reranking

router = APIRouter()


class SearchFilters(BaseModel):
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = None
    document_type: str | None = None
    classification: str | None = None
    language: str | None = None
    latest_version_only: bool = True


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    top_k: int = 10
    rerank: bool = True
    filters: SearchFilters = Field(default_factory=SearchFilters)


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
def search(body: SearchRequest, db: Session = Depends(get_db)):
    top_k = max(1, min(body.top_k, settings.search_max_top_k))

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
    )

    filenames: dict[uuid.UUID, str] = {}
    if hits:
        doc_ids = {h.document_id for h in hits}
        rows = db.query(Document.id, Document.filename).filter(Document.id.in_(doc_ids)).all()
        filenames = {r[0]: r[1] for r in rows}

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
                text=h.text,
                strategy=h.strategy,
                score=h.score,
            )
            for h in hits
        ],
    )
