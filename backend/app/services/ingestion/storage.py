import json
import uuid
from pathlib import Path

from app.core.config import settings
from app.services.ingestion.types import NormalizedImage, NormalizedTable, DocumentMetadata


def _root_dir() -> Path:
    return Path(settings.upload_dir)


def make_document_dir(document_id: uuid.UUID) -> Path:
    doc_dir = _root_dir() / str(document_id)
    (doc_dir / "content" / "tables").mkdir(parents=True, exist_ok=True)
    (doc_dir / "content" / "images").mkdir(parents=True, exist_ok=True)
    (doc_dir / "original").mkdir(parents=True, exist_ok=True)
    return doc_dir


def save_original(doc_dir: Path, filename: str, content: bytes) -> Path:
    path = doc_dir / "original" / filename
    path.write_bytes(content)
    return path


def save_text(doc_dir: Path, text: str) -> Path:
    path = doc_dir / "content" / "text.md"
    path.write_text(text, encoding="utf-8")
    return path


def save_tables(doc_dir: Path, tables: list[NormalizedTable]) -> Path | None:
    if not tables:
        return None
    tables_dir = doc_dir / "content" / "tables"
    manifest = []
    for table in tables:
        filename = f"table_{table.index:03d}.json"
        (tables_dir / filename).write_text(
            json.dumps({"headers": table.headers, "rows": table.rows}, indent=2),
            encoding="utf-8",
        )
        manifest.append({"index": table.index, "file": filename, "caption": table.caption, "page": table.page})
    (tables_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tables_dir


def save_images(doc_dir: Path, images: list[NormalizedImage]) -> Path | None:
    if not images:
        return None
    images_dir = doc_dir / "content" / "images"
    manifest = []
    for image in images:
        filename = f"image_{image.index:03d}.{image.format}"
        (images_dir / filename).write_bytes(image.data)
        manifest.append({"index": image.index, "file": filename, "caption": image.caption, "page": image.page})
    (images_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return images_dir


def save_metadata_json(doc_dir: Path, metadata: DocumentMetadata) -> Path:
    path = doc_dir / "content" / "metadata.json"
    payload = {
        "document_type": metadata.document_type,
        "title": metadata.title,
        "author": metadata.author,
        "creation_date": metadata.creation_date.isoformat() if metadata.creation_date else None,
        "modified_date": metadata.modified_date.isoformat() if metadata.modified_date else None,
        "language": metadata.language,
        "page_count": metadata.page_count,
        "headings": metadata.headings,
        "keywords": metadata.keywords,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
