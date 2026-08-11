from app.services.chunking import text_utils
from app.services.chunking.types import Chunk


def chunk(parsed, config) -> list[Chunk]:
    sections = text_utils.split_sections(parsed.text)
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
                chunk_size_tokens=config.chunk_size_tokens,
                overlap_tokens=0,
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
                        chunk_size_tokens=config.chunk_size_tokens,
                        overlap_tokens=config.chunk_overlap_tokens,
                        extra={"heading": heading, "role": "child"} if heading else {"role": "child"},
                    )
                )
                index += 1

    return chunks
