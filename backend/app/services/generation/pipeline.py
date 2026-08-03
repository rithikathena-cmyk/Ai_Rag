from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.services.generation.client import generate_answer
from app.services.generation.prompt_builder import SYSTEM_PROMPT, Citation, build_user_message
from app.services.reranking.pipeline import search_with_reranking

NO_CONTEXT_ANSWER = (
    "I don't have any documents in the knowledge base that are relevant to this question, "
    "so I can't answer it from the available context."
)
REFUSED_ANSWER = "I'm not able to help with that request."


@dataclass
class ChatAnswer:
    answer: str
    citations: list[Citation]


def answer_query(db: Session, query: str, top_k: int | None = None) -> ChatAnswer:
    hits, _reranked = search_with_reranking(db, query=query, mode="hybrid", top_k=top_k or settings.chat_context_top_k)
    if not hits:
        return ChatAnswer(answer=NO_CONTEXT_ANSWER, citations=[])

    doc_ids = {h.document_id for h in hits}
    filenames = {r[0]: r[1] for r in db.query(Document.id, Document.filename).filter(Document.id.in_(doc_ids)).all()}

    citations = [
        Citation(
            index=i + 1,
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            document_filename=filenames.get(h.document_id),
            chunk_index=h.chunk_index,
            text=h.text,
        )
        for i, h in enumerate(hits)
    ]

    answer_text, stop_reason = generate_answer(SYSTEM_PROMPT, build_user_message(query, citations))
    if stop_reason == "refusal":
        return ChatAnswer(answer=REFUSED_ANSWER, citations=[])

    return ChatAnswer(answer=answer_text, citations=citations)
