import json
import re
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


def sanitize_filename(filename: str) -> str:
    """Reduces a client-supplied filename to a single safe path segment.

    `file.filename` on a multipart upload is attacker-controlled and, before
    this, was joined into a filesystem path unsanitized (`doc_dir / "original"
    / filename`) — a value like "../../../etc/passwd" or (on Windows) an
    absolute path such as "C:\\Windows\\evil.dll" would escape `doc_dir`
    entirely: `Path.__truediv__` does not normalize ".." segments, and joining
    an absolute path onto an existing Path silently *discards* the left side
    per pathlib's own documented behavior, so an absolute-looking filename
    would have replaced the intended directory outright.

    `Path(filename).name` strips every directory component (leading path,
    drive letter, "..", "."), leaving only the final segment — no traversal
    is possible with what's left. A handful of extra characters that are
    illegal or dangerous on at least one target OS (NUL, control chars, the
    Windows-reserved `<>:"|?*`) are stripped too, since this path may be read
    back on a different OS than it was written on.
    """
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "", name).strip(" .")
    return name or "unnamed"


def save_original(doc_dir: Path, filename: str, content: bytes) -> Path:
    safe_name = sanitize_filename(filename)
    path = doc_dir / "original" / safe_name
    # Belt-and-suspenders: confirm the resolved path is still actually inside
    # doc_dir/original before writing anything — a second, independent check
    # that doesn't rely solely on sanitize_filename() having covered every
    # case, since a filesystem write is exactly the kind of consequence this
    # guardrail's "fail closed" principle applies to.
    target_dir = (doc_dir / "original").resolve()
    resolved = path.resolve()
    if resolved.parent != target_dir:
        raise ValueError(f"Rejected unsafe upload filename: {filename!r}")
    resolved.write_bytes(content)
    return resolved


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
