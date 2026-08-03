import json
from pathlib import Path

from lxml import etree

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument, NormalizedTable


def _json_to_table(data) -> NormalizedTable | None:
    if not isinstance(data, list) or not data or not all(isinstance(row, dict) for row in data):
        return None
    headers: list[str] = []
    for row in data:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    rows = [[str(row.get(h, "")) for h in headers] for row in data]
    return NormalizedTable(index=0, headers=headers, rows=rows)


def _parse_json(file_path: Path) -> NormalizedDocument:
    data = json.loads(file_path.read_text(encoding="utf-8"))
    text = json.dumps(data, indent=2)
    table = _json_to_table(data)
    return NormalizedDocument(
        text=text,
        tables=[table] if table else [],
        images=[],
        metadata=DocumentMetadata(document_type=DocumentFormat.JSON.value, title=file_path.stem),
    )


def _parse_xml(file_path: Path) -> NormalizedDocument:
    tree = etree.parse(str(file_path))
    text = etree.tostring(tree, pretty_print=True, encoding="unicode")
    return NormalizedDocument(
        text=text,
        tables=[],
        images=[],
        metadata=DocumentMetadata(document_type=DocumentFormat.XML.value, title=file_path.stem),
    )


def parse(file_path: Path, fmt: DocumentFormat) -> NormalizedDocument:
    if fmt == DocumentFormat.JSON:
        return _parse_json(file_path)
    return _parse_xml(file_path)
