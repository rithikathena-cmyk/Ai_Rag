from app.core.config import settings
from app.services.chunking import function_class, header_based, parent_child, row_based, semantic
from app.services.chunking.types import Chunk
from app.services.ingestion.detector import DocumentFormat

FORMAT_STRATEGY = {
    DocumentFormat.HTML: lambda parsed, config, ext: header_based.chunk(parsed, config),
    DocumentFormat.MARKDOWN: lambda parsed, config, ext: header_based.chunk(parsed, config),
    DocumentFormat.XLSX: lambda parsed, config, ext: row_based.chunk(parsed, config),
    DocumentFormat.CSV: lambda parsed, config, ext: row_based.chunk(parsed, config),
    DocumentFormat.SQL: lambda parsed, config, ext: row_based.chunk(parsed, config, include_schema_chunk=True),
    DocumentFormat.CODE: lambda parsed, config, ext: function_class.chunk(parsed, config, ext),
}


def chunk_document(parsed, fmt: DocumentFormat, file_extension: str) -> list[Chunk]:
    if fmt in FORMAT_STRATEGY:
        return FORMAT_STRATEGY[fmt](parsed, settings, file_extension)

    # No classification step anymore (it was a slow model call) -- pick between
    # the two general-purpose strategies using a structure signal parsing
    # already gives us for free: headings mean the doc has real sections worth
    # a parent/child hierarchy, otherwise it's undifferentiated prose better
    # served by similarity-based grouping.
    if parsed.metadata.headings:
        return parent_child.chunk(parsed, settings)
    return semantic.chunk(parsed, settings)
