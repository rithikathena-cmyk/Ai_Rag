from pathlib import Path

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument


def parse(file_path: Path, fmt: DocumentFormat) -> NormalizedDocument:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return NormalizedDocument(
        text=text,
        tables=[],
        images=[],
        metadata=DocumentMetadata(document_type=fmt.value, title=file_path.name),
    )
