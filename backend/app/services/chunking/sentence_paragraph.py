import re

from app.services.chunking import text_utils
from app.services.chunking.types import Chunk

_QA_RE = re.compile(r"^\s*Q[:.]", re.I)
_QA_SPLIT_RE = re.compile(r"(?=\bQ[:.]\s)", re.I)


def _split_units(paragraph: str) -> list[str]:
    # Some parsers (e.g. Docling's PDF text extraction) merge what were
    # visually separate lines into one run-on paragraph with no blank-line
    # breaks, so "Q:" markers can appear mid-paragraph rather than at its
    # start. Split on those boundaries too, not just blank lines.
    parts = _QA_SPLIT_RE.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def chunk(parsed, config) -> list[Chunk]:
    paragraphs = text_utils.split_paragraphs(parsed.text)
    if not paragraphs:
        return []

    units = [unit for para in paragraphs for unit in _split_units(para)]

    chunks: list[Chunk] = []
    current = ""
    current_tokens = 0

    def flush():
        nonlocal current, current_tokens
        if current.strip():
            chunks.append(Chunk(index=len(chunks), text=current.strip(), strategy="sentence_paragraph",
                                 token_count=text_utils.count_tokens(current)))
        current, current_tokens = "", 0

    for unit in units:
        if _QA_RE.match(unit):
            flush()
            chunks.append(Chunk(index=len(chunks), text=unit, strategy="sentence_paragraph",
                                 token_count=text_utils.count_tokens(unit), extra={"kind": "qa_pair"}))
            continue

        unit_tokens = text_utils.count_tokens(unit)
        if current and current_tokens + unit_tokens > config.chunk_size_tokens:
            flush()
        current = (current + "\n\n" + unit).strip() if current else unit
        current_tokens = text_utils.count_tokens(current)

    flush()
    return chunks
