from app.services.chunking import text_utils
from app.services.chunking.types import Chunk


def chunk(parsed, config, overlap_ratio: float | None = None) -> list[Chunk]:
    overlap_ratio = overlap_ratio if overlap_ratio is not None else config.default_overlap_ratio
    child_overlap_tokens = int(config.chunk_size_tokens * overlap_ratio)

    chunks: list[Chunk] = []
    index = 0

    # Split on markdown heading boundaries first so a parent/child chunk never
    # merges text from two unrelated sections (e.g. a conceptual explanation
    # glued to setup instructions) just because both fit under the token
    # budget — size-based recursive_split alone is heading-blind.
    for heading, section_text in text_utils.split_sections(parsed.text):
        parent_texts = text_utils.recursive_split(section_text, config.chunk_size_tokens_parent, 0)

        for parent_text in parent_texts:
            parent_index = index
            chunks.append(
                Chunk(
                    index=parent_index,
                    text=parent_text,
                    strategy="parent_child",
                    token_count=text_utils.count_tokens(parent_text),
                    chunk_size_tokens=config.chunk_size_tokens_parent,
                    overlap_tokens=0,
                    extra={"heading": heading, "role": "parent"} if heading else {"role": "parent"},
                )
            )
            index += 1

            if text_utils.count_tokens(parent_text) <= config.chunk_size_tokens:
                continue

            child_texts = text_utils.recursive_split(parent_text, config.chunk_size_tokens, child_overlap_tokens)
            for child_text in child_texts:
                chunks.append(
                    Chunk(
                        index=index,
                        text=child_text,
                        strategy="parent_child",
                        parent_index=parent_index,
                        token_count=text_utils.count_tokens(child_text),
                        chunk_size_tokens=config.chunk_size_tokens,
                        overlap_tokens=child_overlap_tokens,
                        extra={"heading": heading, "role": "child"} if heading else {"role": "child"},
                    )
                )
                index += 1

    return chunks
