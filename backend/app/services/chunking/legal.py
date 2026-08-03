from types import SimpleNamespace

from app.services.chunking import semantic, text_utils
from app.services.chunking.types import Chunk


def chunk(parsed, config) -> list[Chunk]:
    parent_config = SimpleNamespace(
        chunk_size_tokens=config.chunk_size_tokens_parent,
        semantic_similarity_threshold=config.semantic_similarity_threshold,
    )
    parent_chunks = semantic.chunk(parsed, parent_config, strategy_name="legal")

    chunks: list[Chunk] = []
    index = 0
    child_overlap_tokens = int(config.chunk_size_tokens * config.default_overlap_ratio)

    for parent in parent_chunks:
        parent_index = index
        parent.index = parent_index
        parent.extra = {"role": "parent"}
        chunks.append(parent)
        index += 1

        if parent.token_count <= config.chunk_size_tokens:
            continue

        child_texts = text_utils.recursive_split(parent.text, config.chunk_size_tokens, child_overlap_tokens)
        for child_text in child_texts:
            chunks.append(
                Chunk(
                    index=index,
                    text=child_text,
                    strategy="legal",
                    parent_index=parent_index,
                    token_count=text_utils.count_tokens(child_text),
                    extra={"role": "child"},
                )
            )
            index += 1

    return chunks
