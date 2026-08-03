import uuid
from dataclasses import dataclass

SYSTEM_PROMPT = """You are a retrieval-augmented assistant answering questions about the user's uploaded documents.

Answer using ONLY the numbered context excerpts provided in the user message. Do not use outside knowledge, even if you know the answer.

When you use information from an excerpt, cite it inline with its bracketed number, e.g. [1] or [2][3]. Cite every factual claim you make.

If the excerpts don't contain enough information to answer, say so plainly instead of guessing."""


@dataclass
class Citation:
    index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str | None
    chunk_index: int
    text: str


def _context_block(citation: Citation) -> str:
    label = citation.document_filename or str(citation.document_id)
    return f'[{citation.index}] (source: "{label}")\n{citation.text}'


def build_user_message(query: str, citations: list[Citation]) -> str:
    blocks = "\n\n".join(_context_block(c) for c in citations)
    return f"Context excerpts:\n\n{blocks}\n\nQuestion: {query}"
