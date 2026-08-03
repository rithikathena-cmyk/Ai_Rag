import re

from app.services.chunking import text_utils
from app.services.chunking.types import Chunk

_HEADER_KEY_RE = re.compile(r"(?i)\b(from|to|subject|date|cc|bcc)\s*:\s*")
_SALUTATION_RE = re.compile(r"(?i)\b(hi|hello|hey|dear)\b\s*\w*,?")
_QUOTE_MARKER_RE = re.compile(r"(?m)^(>|On .+ wrote:)")


def _extract_headers(text: str) -> tuple[dict, str]:
    # Header key/value boundaries are found by position, not by line, since some
    # parsers (e.g. Docling's PDF extraction) merge visually-separate header
    # lines into one continuous run of text with no line breaks between them.
    head_window = min(len(text), 600)
    head_text = text[:head_window]
    matches = list(_HEADER_KEY_RE.finditer(head_text))
    if not matches:
        return {}, text.strip()

    headers = {}
    last_value_end = 0
    for i, m in enumerate(matches):
        key = m.group(1).lower()
        value_start = m.end()
        if i + 1 < len(matches):
            value_end = matches[i + 1].start()
        else:
            salutation = _SALUTATION_RE.search(head_text, value_start)
            value_end = salutation.start() if salutation else min(value_start + 80, head_window)
        headers[key] = head_text[value_start:value_end].strip().rstrip(",")
        last_value_end = value_end

    body = text[last_value_end:].strip()
    return headers, body


def chunk(parsed, config) -> list[Chunk]:
    headers, body = _extract_headers(parsed.text)
    if not body:
        body = parsed.text

    quote_matches = list(_QUOTE_MARKER_RE.finditer(body))
    if quote_matches:
        segments = []
        prev_end = 0
        for m in quote_matches:
            segments.append(body[prev_end : m.start()].strip())
            prev_end = m.start()
        segments.append(body[prev_end:].strip())
        segments = [s for s in segments if s]
    else:
        segments = text_utils.recursive_split(body, config.chunk_size_tokens, config.chunk_overlap_tokens)

    chunks = []
    for i, segment in enumerate(segments):
        extra = {"headers": headers} if i == 0 and headers else {}
        chunks.append(
            Chunk(index=i, text=segment, strategy="thread", token_count=text_utils.count_tokens(segment), extra=extra)
        )
    return chunks
