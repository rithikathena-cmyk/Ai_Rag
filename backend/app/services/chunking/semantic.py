import numpy as np

from app.services.chunking import text_utils
from app.services.chunking.types import Chunk
from app.services.embedding.model_loader import embed_texts


def chunk(parsed, config, strategy_name: str = "semantic") -> list[Chunk]:
    sentences = text_utils.split_sentences(parsed.text)
    if not sentences:
        return []
    if len(sentences) == 1:
        return [Chunk(index=0, text=sentences[0], strategy=strategy_name,
                       token_count=text_utils.count_tokens(sentences[0]),
                       chunk_size_tokens=config.chunk_size_tokens, overlap_tokens=0)]

    vectors = np.array(embed_texts(sentences))

    groups: list[list[str]] = [[sentences[0]]]
    group_tokens = text_utils.count_tokens(sentences[0])

    for i in range(1, len(sentences)):
        similarity = float(np.dot(vectors[i - 1], vectors[i]))
        sentence_tokens = text_utils.count_tokens(sentences[i])
        if similarity < config.semantic_similarity_threshold or group_tokens + sentence_tokens > config.chunk_size_tokens:
            groups.append([sentences[i]])
            group_tokens = sentence_tokens
        else:
            groups[-1].append(sentences[i])
            group_tokens += sentence_tokens

    chunks = []
    for i, group in enumerate(groups):
        text = " ".join(group)
        if chunks and text_utils.count_tokens(text) < config.chunk_size_tokens * 0.15:
            chunks[-1].text += " " + text
            chunks[-1].token_count = text_utils.count_tokens(chunks[-1].text)
            continue
        chunks.append(Chunk(index=len(chunks), text=text, strategy=strategy_name, token_count=text_utils.count_tokens(text),
                             chunk_size_tokens=config.chunk_size_tokens, overlap_tokens=0))

    for i, c in enumerate(chunks):
        c.index = i

    return chunks
