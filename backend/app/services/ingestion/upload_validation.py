"""Magic-byte sniffing so an upload's actual content is checked against the
format its filename extension claims, catching a renamed/mislabeled file
(malicious or accidental) that `detect_format`'s extension lookup alone can't.

Text-based formats (txt/md/html/csv/json/xml/sql/code) have no reliable
binary signature, so they're accepted on extension alone here — the same as
before this module existed.
"""

from app.services.ingestion.detector import DocumentFormat

_TEXT_FORMATS: frozenset[DocumentFormat] = frozenset(
    {
        DocumentFormat.TXT,
        DocumentFormat.MARKDOWN,
        DocumentFormat.HTML,
        DocumentFormat.CSV,
        DocumentFormat.JSON,
        DocumentFormat.XML,
        DocumentFormat.SQL,
        DocumentFormat.CODE,
    }
)

# Signature -> the formats it's valid evidence for. Office Open XML formats
# (docx/pptx/xlsx) are all zip containers, so they share the same leading
# bytes and can't be told apart without unzipping — that's left to the
# Docling/tabular parsers that run downstream.
_MAGIC_SIGNATURES: tuple[tuple[bytes, frozenset[DocumentFormat]], ...] = (
    (b"%PDF-", frozenset({DocumentFormat.PDF})),
    (b"PK\x03\x04", frozenset({DocumentFormat.DOCX, DocumentFormat.PPTX, DocumentFormat.XLSX})),
    (b"\x89PNG\r\n\x1a\n", frozenset({DocumentFormat.IMAGE})),
    (b"\xff\xd8\xff", frozenset({DocumentFormat.IMAGE})),
    (b"II*\x00", frozenset({DocumentFormat.IMAGE})),
    (b"MM\x00*", frozenset({DocumentFormat.IMAGE})),
    (b"BM", frozenset({DocumentFormat.IMAGE})),
)


def validate_mime(fmt: DocumentFormat, content: bytes) -> bool:
    """True if `content`'s magic bytes are consistent with `fmt`. Formats with
    no reliable signature (plain text) always pass."""
    if fmt in _TEXT_FORMATS:
        return True
    for signature, formats in _MAGIC_SIGNATURES:
        if content.startswith(signature):
            return fmt in formats
    return False
