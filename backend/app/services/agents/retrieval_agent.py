import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import DocumentModel
from app.services.reranking.pipeline import search_with_reranking
from app.services.retrieval.search import fetch_parent_context


def search_documents(
    db: Session,
    *,
    query: str,
    top_k: int | None = None,
    document_type: str | None = None,
    classification: str | None = None,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    knowledge_departments: tuple[str, ...] | None = None,
    allow_unfiltered: bool = False,
) -> list[dict]:
    """Hybrid (dense + sparse, reranked) search over Qdrant with optional metadata filters.

    `user_id`, when supplied, narrows results through the retrieval
    permission rail (services/guardrails/retrieval_permissions.py). `role` +
    `knowledge_departments`, when supplied, narrow results through the
    LLM-RBAC category rail (same module, apply_category_policy()) — both
    filters are applied before anything reaches the caller, i.e. before it
    ever reaches the LLM's context window."""
    hits, _reranked = search_with_reranking(
        db,
        query=query,
        mode="hybrid",
        top_k=top_k or settings.chat_context_top_k,
        document_type=document_type,
        classification=classification,
        user_id=user_id,
        role=role,
        knowledge_departments=knowledge_departments,
        allow_unfiltered=allow_unfiltered,
    )
    if not hits:
        return []

    doc_ids = {h.document_id for h in hits}
    filenames = {r[0]: r[1] for r in db.query(DocumentModel.id, DocumentModel.filename).filter(DocumentModel.id.in_(doc_ids)).all()}

    # Phase 3A — parent-child retrieval (docs/RAG_RETRIEVAL.md): attaches
    # broader parent-section context to a precisely-matched child chunk
    # without changing which chunk was retrieved/cited (see
    # fetch_parent_context()'s own docstring for the authorization argument).
    # `hits` is already permission-filtered and reranked at this point, so
    # this is a same-document read, not a new access grant.
    parent_context_by_chunk_id = (
        fetch_parent_context(
            db, hits,
            max_expansions=settings.parent_context_max_expansions,
            max_chars=settings.parent_context_max_chars,
        )
        if settings.parent_child_retrieval_enabled
        else {}
    )

    # No "index" field here — the caller (planner) assigns a globally unique
    # citation number across the whole conversation, since multiple search
    # calls (parallel or across turns) must not collide on the same number.
    #
    # Every item below carries BOTH representations of its text —
    # `text`/`parent_context` (original, authorized content) and
    # `display_text`/`parent_context_display` (redact_pii()'d) — deliberately
    # NOT resolved to a single value here. This function has no LLM in the
    # loop and no persistence responsibility of its own; the caller does
    # (services/agents/planner.py's search_documents tool splits this into
    # the LLM-facing payload vs. the persisted/returned `sources` list — see
    # its module docstring), so it's the one place that gets to choose. See
    # services/guardrails/pii.py::DualText for the raw/display contract this
    # mirrors.
    results = []
    for h in hits:
        item = {
            "chunk_id": str(h.chunk_id),
            "document_id": str(h.document_id),
            "document_filename": filenames.get(h.document_id),
            "chunk_index": h.chunk_index,
            "text": h.text,
            "display_text": h.display_text,
            "score": h.score,
        }
        # `text` stays the precisely-matched chunk — the citation still
        # points at exactly this evidence. parent_context is supplementary
        # background for the LLM, never itself a separately numbered source.
        parent = parent_context_by_chunk_id.get(h.chunk_id)
        if parent:
            item["parent_context"] = parent.raw
            item["parent_context_display"] = parent.display
        results.append(item)
    return results
