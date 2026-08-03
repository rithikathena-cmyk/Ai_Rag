from app.services.chunking import text_utils
from app.services.chunking.types import Chunk


def chunk(parsed, config, overlap_ratio: float | None = None) -> list[Chunk]:
    overlap_ratio = overlap_ratio if overlap_ratio is not None else config.default_overlap_ratio
    child_overlap_tokens = int(config.chunk_size_tokens * overlap_ratio)

    parent_texts = text_utils.recursive_split(parsed.text, config.chunk_size_tokens_parent, 0)

    chunks: list[Chunk] = []
    index = 0
    for parent_text in parent_texts:
        parent_index = index
        chunks.append(
            Chunk(
                index=parent_index,
                text=parent_text,
                strategy="parent_child",
                token_count=text_utils.count_tokens(parent_text),
                extra={"role": "parent"},
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
                    extra={"role": "child"},
                )
            )
            index += 1

    return chunks
