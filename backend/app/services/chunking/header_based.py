import re

from app.services.chunking import text_utils
from app.services.chunking.types import Chunk

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _split_sections(text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)] if text.strip() else []

    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((heading, text[start:end].strip()))

    return sections


def chunk(parsed, config) -> list[Chunk]:
    sections = _split_sections(parsed.text)
    if not sections:
        return []

    chunks: list[Chunk] = []
    index = 0

    for heading, section_text in sections:
        section_tokens = text_utils.count_tokens(section_text)
        section_index = index
        chunks.append(
            Chunk(
                index=section_index,
                text=section_text,
                strategy="header_based",
                token_count=section_tokens,
                extra={"heading": heading} if heading else {},
            )
        )
        index += 1

        if section_tokens > config.chunk_size_tokens:
            chunks[-1].extra["role"] = "parent"
            child_texts = text_utils.recursive_split(section_text, config.chunk_size_tokens, config.chunk_overlap_tokens)
            for child_text in child_texts:
                chunks.append(
                    Chunk(
                        index=index,
                        text=child_text,
                        strategy="header_based",
                        parent_index=section_index,
                        token_count=text_utils.count_tokens(child_text),
                        extra={"heading": heading, "role": "child"} if heading else {"role": "child"},
                    )
                )
                index += 1

    return chunks
