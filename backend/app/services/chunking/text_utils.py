import re
import threading
import time

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

# Thread-local so concurrent uploads (each on its own FastAPI threadpool
# worker) don't clobber each other's running total. A caller brackets one
# chunk_document() call with reset_tokenize_timer()/get_tokenize_time_ms()
# to measure how much of that call's wall time was spent inside the HF
# tokenizer specifically, as opposed to the rest of the chunking logic.
_tokenize_timer = threading.local()


def reset_tokenize_timer() -> None:
    _tokenize_timer.total_ms = 0.0


def get_tokenize_time_ms() -> float:
    return getattr(_tokenize_timer, "total_ms", 0.0)


def split_sections(text: str) -> list[tuple[str, str]]:
    """Splits markdown text into (heading, section_text) pairs at `#`-`######`
    boundaries. A document with no headings comes back as a single ("", text)
    section rather than an empty list, so callers can treat it uniformly."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)] if text.strip() else []

    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((heading, text[start:end].strip()))

    return sections


def count_tokens(text: str) -> int:
    if not text:
        return 0
    from app.services.embedding.model_loader import get_tokenizer

    start = time.perf_counter()
    try:
        return len(get_tokenizer().encode(text, add_special_tokens=False))
    except Exception:
        return max(1, len(text) // 4)
    finally:
        if hasattr(_tokenize_timer, "total_ms"):
            _tokenize_timer.total_ms += (time.perf_counter() - start) * 1000


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
                current_tokens = count_tokens(overlap_text) + piece_tokens
            else:
                current = piece
                current_tokens = piece_tokens
        else:
            # Track the running count incrementally instead of re-tokenizing
            # the whole (growing) `current` string on every piece — for a
            # long document being merged into many chunks, that re-tokenize
            # was O(pieces x chunk size) HF tokenizer calls instead of O(pieces).
            # This is a slight approximation (BPE can merge differently right
            # at a join point) but that's within the noise of an already-soft
            # token budget.
            current = (current + joiner + piece).strip() if current else piece
            current_tokens += piece_tokens

    if current.strip():
        merged.append(current.strip())

    return merged


def _tail_by_tokens(text: str, target_tokens: int) -> str:
    approx_chars = max(1, target_tokens * 4)
    return text[-approx_chars:]
