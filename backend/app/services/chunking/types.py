from dataclasses import dataclass, field


@dataclass
class Chunk:
    index: int
    text: str
    strategy: str
    parent_index: int | None = None
    token_count: int = 0
    extra: dict = field(default_factory=dict)
