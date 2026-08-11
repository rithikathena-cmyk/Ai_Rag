from dataclasses import dataclass, field


@dataclass
class Chunk:
    index: int
    text: str
    strategy: str
    parent_index: int | None = None
    token_count: int = 0
    chunk_size_tokens: int | None = None
    overlap_tokens: int | None = None
    extra: dict = field(default_factory=dict)
