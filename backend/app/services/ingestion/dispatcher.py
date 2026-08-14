import logging
from pathlib import Path

from app.core.config import settings
from app.services.ingestion import (
    code_parser,
    docling_parser,
    metadata_extractor,
    plaintext_parser,
    pymupdf_parser,
    sql_parser,
    structured_parser,
    tabular_parser,
)
from app.services.ingestion.detector import DocumentFormat, detect_format
from app.services.ingestion.types import DocumentParsingError, NormalizedDocument, UnsupportedFormatError

logger = logging.getLogger(__name__)

DOCLING_FORMATS = {
    DocumentFormat.DOCX,
    DocumentFormat.PPTX,
    DocumentFormat.HTML,
    DocumentFormat.IMAGE,
}


def _chars_per_page(doc: NormalizedDocument) -> float:
    return len(doc.text.strip()) / (doc.metadata.page_count or 1)


def parse_document(file_path: Path, filename: str, content_type: str | None) -> NormalizedDocument:
    fmt = detect_format(filename, content_type)
    if fmt is None:
        raise UnsupportedFormatError(filename)

    try:
        if fmt == DocumentFormat.PDF:
            # PyMuPDF has no OCR. Try it first (fast path for normal, already-
            # text-bearing PDFs); if it comes back with near-zero text — the
            # signature of a scanned/image-only page — fall back to Docling's
            # OCR pipeline for this document only, rather than paying OCR cost
            # on every PDF.
            doc = pymupdf_parser.parse(file_path)
            if settings.pdf_ocr_fallback_enabled and _chars_per_page(doc) < settings.pdf_ocr_fallback_min_chars_per_page:
                logger.info(
                    "PDF %r yielded %.1f chars/page over %s page(s) — below the OCR fallback "
                    "threshold, re-parsing with Docling OCR",
                    filename, _chars_per_page(doc), doc.metadata.page_count,
                )
                try:
                    doc = docling_parser.parse(file_path, fmt)
                except Exception:
                    # Keep the PyMuPDF result — a low-quality parse is still
                    # better than failing the whole upload over a fallback
                    # that didn't pan out.
                    logger.warning("Docling OCR fallback failed for %r; keeping PyMuPDF result", filename, exc_info=True)
        elif fmt in DOCLING_FORMATS:
            doc = docling_parser.parse(file_path, fmt)
        elif fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TXT):
            doc = plaintext_parser.parse(file_path, fmt)
        elif fmt in (DocumentFormat.XLSX, DocumentFormat.CSV):
            doc = tabular_parser.parse(file_path, fmt)
        elif fmt in (DocumentFormat.JSON, DocumentFormat.XML):
            doc = structured_parser.parse(file_path, fmt)
        elif fmt == DocumentFormat.CODE:
            doc = code_parser.parse(file_path, fmt)
        else:
            doc = sql_parser.parse(file_path)
    except Exception as exc:
        raise DocumentParsingError(f"Failed to parse {filename} as {fmt.value}: {exc}") from exc

    doc.metadata.language = metadata_extractor.detect_language(doc.text)
    doc.metadata.keywords = metadata_extractor.extract_keywords(doc.text, doc.metadata.language)
    doc.metadata.document_type = fmt.value
    return doc
