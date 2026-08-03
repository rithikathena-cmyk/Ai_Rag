from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.services.reranking.pipeline import search_with_reranking


def search_documents(
    db: Session,
    *,
    query: str,
    top_k: int | None = None,
    document_type: str | None = None,
    classification: str | None = None,
) -> list[dict]:
    """Hybrid (dense + sparse, reranked) search over Qdrant with optional metadata filters."""
    hits, _reranked = search_with_reranking(
        db,
        query=query,
        mode="hybrid",
        top_k=top_k or settings.chat_context_top_k,
        document_type=document_type,
        classification=classification,
    )
    if not hits:
        return []

    doc_ids = {h.document_id for h in hits}
    filenames = {r[0]: r[1] for r in db.query(Document.id, Document.filename).filter(Document.id.in_(doc_ids)).all()}

    # No "index" field here — the caller (planner) assigns a globally unique
    # citation number across the whole conversation, since multiple search
    # calls (parallel or across turns) must not collide on the same number.
    return [
        {
            "chunk_id": str(h.chunk_id),
            "document_id": str(h.document_id),
            "document_filename": filenames.get(h.document_id),
            "chunk_index": h.chunk_index,
            "text": h.text,
            "score": h.score,
        }
        for h in hits
    ]
