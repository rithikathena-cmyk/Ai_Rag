from pathlib import Path

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

DOCLING_FORMATS = {
    DocumentFormat.DOCX,
    DocumentFormat.PPTX,
    DocumentFormat.HTML,
    DocumentFormat.IMAGE,
}


def parse_document(file_path: Path, filename: str, content_type: str | None) -> NormalizedDocument:
    fmt = detect_format(filename, content_type)
    if fmt is None:
        raise UnsupportedFormatError(filename)

    try:
        if fmt == DocumentFormat.PDF:
            # Docling's OCR fallback is disabled for now for speed — PyMuPDF
            # only, even for scanned/low-text PDFs.
            doc = pymupdf_parser.parse(file_path)
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
