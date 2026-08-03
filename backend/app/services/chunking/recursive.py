from app.services.chunking import text_utils
from app.services.chunking.types import Chunk


def chunk(parsed, config, strategy_name: str = "recursive") -> list[Chunk]:
    pieces = text_utils.recursive_split(parsed.text, config.chunk_size_tokens, config.chunk_overlap_tokens)
    return [
        Chunk(index=i, text=piece, strategy=strategy_name, token_count=text_utils.count_tokens(piece))
        for i, piece in enumerate(pieces)
    ]
