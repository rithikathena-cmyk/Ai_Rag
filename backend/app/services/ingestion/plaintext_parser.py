import re
from pathlib import Path

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def parse(file_path: Path, fmt: DocumentFormat) -> NormalizedDocument:
    text = file_path.read_text(encoding="utf-8", errors="replace")

    headings: list[str] = []
    if fmt == DocumentFormat.MARKDOWN:
        headings = [m.group(1).strip() for m in _HEADING_RE.finditer(text)]

    title = headings[0] if headings else file_path.stem

    return NormalizedDocument(
        text=text,
        tables=[],
        images=[],
        metadata=DocumentMetadata(
            document_type=fmt.value,
            title=title,
            headings=headings,
        ),
    )
