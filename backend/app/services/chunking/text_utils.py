import re

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    from app.services.embedding.model_loader import get_tokenizer

    try:
        return len(get_tokenizer().encode(text, add_special_tokens=False))
    except Exception:
        return max(1, len(text) // 4)


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    sentences = _SENTENCE_RE.split(text)
    return [s.strip() for s in sentences if s.strip()]


def split_paragraphs(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [p.strip() for p in _PARAGRAPH_RE.split(text) if p.strip()]


def recursive_split(
    text: str,
    max_tokens: int,
    overlap_tokens: int = 0,
    separators: list[str] | None = None,
) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= max_tokens:
        return [text]

    separators = separators if separators is not None else ["\n\n", "\n", ". ", " "]

    if not separators:
        pieces = _hard_split_by_chars(text, max_tokens)
    else:
        sep = separators[0]
        rest_separators = separators[1:]
        parts = [p for p in text.split(sep) if p.strip()] if sep else [text]
        if len(parts) <= 1:
            pieces = recursive_split(text, max_tokens, overlap_tokens, rest_separators)
        else:
            pieces = []
            for part in parts:
                pieces.extend(recursive_split(part, max_tokens, 0, rest_separators))

    return _merge_with_overlap(pieces, max_tokens, overlap_tokens, separators[0] if separators else " ")


def _hard_split_by_chars(text: str, max_tokens: int) -> list[str]:
    approx_chars = max(1, max_tokens * 4)
    return [text[i : i + approx_chars] for i in range(0, len(text), approx_chars)]


def _merge_with_overlap(pieces: list[str], max_tokens: int, overlap_tokens: int, joiner: str) -> list[str]:
    if not pieces:
        return []

    merged: list[str] = []
    current = ""
    current_tokens = 0

    for piece in pieces:
        piece_tokens = count_tokens(piece)
        if current and current_tokens + piece_tokens > max_tokens:
            merged.append(current.strip())
            if overlap_tokens > 0:
                overlap_text = _tail_by_tokens(current, overlap_tokens)
                current = (overlap_text + joiner + piece).strip()
                current_tokens = count_tokens(current)
            else:
                current = piece
                current_tokens = piece_tokens
        else:
            current = (current + joiner + piece).strip() if current else piece
            current_tokens = count_tokens(current)

    if current.strip():
        merged.append(current.strip())

    return merged


def _tail_by_tokens(text: str, target_tokens: int) -> str:
    approx_chars = max(1, target_tokens * 4)
    return text[-approx_chars:]
