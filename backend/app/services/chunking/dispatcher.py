from app.core.config import settings
from app.services.chunking import (
    conversation,
    function_class,
    header_based,
    legal,
    parent_child,
    recursive,
    row_based,
    semantic,
    sentence_paragraph,
    thread,
)
from app.services.chunking.types import Chunk
from app.services.classification.types import ClassificationResult
from app.services.ingestion.detector import DocumentFormat

FORMAT_STRATEGY = {
    DocumentFormat.HTML: lambda parsed, config, ext: header_based.chunk(parsed, config),
    DocumentFormat.MARKDOWN: lambda parsed, config, ext: header_based.chunk(parsed, config),
    DocumentFormat.XLSX: lambda parsed, config, ext: row_based.chunk(parsed, config),
    DocumentFormat.CSV: lambda parsed, config, ext: row_based.chunk(parsed, config),
    DocumentFormat.SQL: lambda parsed, config, ext: row_based.chunk(parsed, config, include_schema_chunk=True),
    DocumentFormat.CODE: lambda parsed, config, ext: function_class.chunk(parsed, config, ext),
}

CLASSIFICATION_STRATEGY = {
    "Manual": lambda parsed, config: parent_child.chunk(parsed, config, overlap_ratio=config.manual_overlap_ratio),
    "SOP": lambda parsed, config: parent_child.chunk(parsed, config, overlap_ratio=config.default_overlap_ratio),
    "Company Policy": lambda parsed, config: recursive.chunk(parsed, config),
    "Research Paper": lambda parsed, config: semantic.chunk(parsed, config),
    "Legal": lambda parsed, config: legal.chunk(parsed, config),
    "FAQ": lambda parsed, config: sentence_paragraph.chunk(parsed, config),
    "Chat Log": lambda parsed, config: conversation.chunk(parsed, config),
    "Email": lambda parsed, config: thread.chunk(parsed, config),
}


def chunk_document(
    parsed, fmt: DocumentFormat, classification: ClassificationResult | None, file_extension: str
) -> list[Chunk]:
    if fmt in FORMAT_STRATEGY:
        return FORMAT_STRATEGY[fmt](parsed, settings, file_extension)

    label = classification.label if classification else None
    strategy_fn = CLASSIFICATION_STRATEGY.get(label, recursive.chunk)
    return strategy_fn(parsed, settings)
