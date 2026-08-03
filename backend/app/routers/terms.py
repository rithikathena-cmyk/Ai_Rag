import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.postgres import get_db
from app.models.chunk import ChunkModel
from app.models.chunk_term_frequency import ChunkTermFrequencyModel
from app.models.term import TermModel

router = APIRouter()


class ChunkTermHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    term_frequency: int
    text_preview: str


class TermChunksResponse(BaseModel):
    term: str
    term_id: int | None
    total_chunks: int
    items: list[ChunkTermHit]


class ChunkTermRow(BaseModel):
    term: str
    term_frequency: int


@router.get("/terms/{term}/chunks", response_model=TermChunksResponse)
def get_term_chunks(term: str, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    normalized = term.strip().lower()
    term_row = db.query(TermModel).filter(TermModel.term == normalized).one_or_none()
    if term_row is None:
        return TermChunksResponse(term=normalized, term_id=None, total_chunks=0, items=[])

    query = (
        db.query(ChunkTermFrequencyModel, ChunkModel)
        .join(ChunkModel, ChunkModel.id == ChunkTermFrequencyModel.chunk_id)
        .filter(ChunkTermFrequencyModel.term_id == term_row.id)
        .order_by(ChunkTermFrequencyModel.term_frequency.desc())
    )
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    items = [
        ChunkTermHit(
            chunk_id=c.id,
            document_id=c.document_id,
            chunk_index=c.chunk_index,
            term_frequency=tf.term_frequency,
            text_preview=c.text[:200],
        )
        for tf, c in rows
    ]
    return TermChunksResponse(term=normalized, term_id=term_row.id, total_chunks=total, items=items)


@router.get("/chunks/{chunk_id}/terms", response_model=list[ChunkTermRow])
def get_chunk_terms(chunk_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = (
        db.query(ChunkTermFrequencyModel, TermModel)
        .join(TermModel, TermModel.id == ChunkTermFrequencyModel.term_id)
        .filter(ChunkTermFrequencyModel.chunk_id == chunk_id)
        .order_by(ChunkTermFrequencyModel.term_frequency.desc())
        .all()
    )
    return [ChunkTermRow(term=t.term, term_frequency=tf.term_frequency) for tf, t in rows]
