from app.services.chunking import text_utils
from app.services.chunking.types import Chunk
from app.services.classification.rules import TURN_RE

_TURNS_PER_CHUNK = 8


def _split_turns(text: str) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip()]
    turns: list[str] = []
    current: list[str] = []
    for line in lines:
        if TURN_RE.match(line) and current:
            turns.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        turns.append("\n".join(current))
    return turns


def chunk(parsed, config) -> list[Chunk]:
    turns = _split_turns(parsed.text)
    if not turns:
        return []

    chunks = []
    for i in range(0, len(turns), _TURNS_PER_CHUNK):
        batch = turns[i : i + _TURNS_PER_CHUNK]
        text = "\n".join(batch)
        chunks.append(
            Chunk(index=len(chunks), text=text, strategy="conversation", token_count=text_utils.count_tokens(text),
                  extra={"turn_range": [i, i + len(batch)]})
        )
    return chunks
