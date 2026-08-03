import uuid

from app.models.chunk import ChunkModel
from app.services.chunking.types import Chunk


def build_chunk_rows(document_id: uuid.UUID, chunks: list[Chunk], embedding_model: str) -> list[ChunkModel]:
    ids = [uuid.uuid4() for _ in chunks]
    rows = []
    for i, c in enumerate(chunks):
        parent_id = ids[c.parent_index] if c.parent_index is not None else None
        rows.append(
            ChunkModel(
                id=ids[i],
                document_id=document_id,
                chunk_index=c.index,
                parent_chunk_id=parent_id,
                text=c.text,
                token_count=c.token_count,
                strategy=c.strategy,
                extra=c.extra,
                qdrant_point_id=str(ids[i]),
                embedding_model=embedding_model,
            )
        )
    return rows
