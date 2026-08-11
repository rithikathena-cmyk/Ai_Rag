from datetime import datetime
from pathlib import Path

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument, NormalizedImage, NormalizedTable

_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter

        _converter = DocumentConverter()
    return _converter


def warm() -> None:
    """Loads Docling's PDF pipeline (layout, table structure, OCR models) once at
    process startup instead of on the first upload. Without this, the first
    PDF/DOCX/PPTX/HTML/image upload pays for both the HF Hub download and the
    CPU model init inline — easily several minutes — and can blow past a
    client's upload timeout even though the parse itself would succeed."""
    from docling.datamodel.base_models import InputFormat

    _get_converter().initialize_pipeline(InputFormat.PDF)


def _to_normalized_table(index: int, table, dl_doc) -> NormalizedTable:
    try:
        df = table.export_to_dataframe(dl_doc)
        headers = [str(c) for c in df.columns]
        rows = df.astype(str).values.tolist()
    except Exception:
        headers, rows = [], []
    caption = None
    try:
        if table.captions:
            caption = table.captions[0].resolve(dl_doc).text
    except Exception:
        pass
    return NormalizedTable(index=index, headers=headers, rows=rows, caption=caption)


def _to_normalized_image(index: int, picture, dl_doc) -> NormalizedImage | None:
    try:
        pil_image = picture.get_image(dl_doc)
        if pil_image is None:
            return None
        import io

        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return NormalizedImage(index=index, data=buf.getvalue(), format="png")
    except Exception:
        return None


def extract_container_metadata(file_path: Path, fmt: DocumentFormat) -> tuple[str | None, str | None, datetime | None, datetime | None]:
    title = author = None
    creation_date = modified_date = None
    try:
        if fmt == DocumentFormat.PDF:
            from pypdf import PdfReader

            info = PdfReader(str(file_path)).metadata
            if info:
                title = info.title
                author = info.author
        elif fmt == DocumentFormat.DOCX:
            from docx import Document as DocxDocument

            props = DocxDocument(str(file_path)).core_properties
            title, author = props.title or None, props.author or None
            creation_date, modified_date = props.created, props.modified
        elif fmt == DocumentFormat.PPTX:
            from pptx import Presentation

            props = Presentation(str(file_path)).core_properties
            title, author = props.title or None, props.author or None
            creation_date, modified_date = props.created, props.modified
    except Exception:
        pass
    return title, author, creation_date, modified_date


def parse(file_path: Path, fmt: DocumentFormat) -> NormalizedDocument:
    result = _get_converter().convert(str(file_path))
    dl_doc = result.document

    text = dl_doc.export_to_markdown()

    headings = []
    try:
        for item, _level in dl_doc.iterate_items():
            label = getattr(item, "label", None)
            label_value = getattr(label, "value", label)
            if label_value in ("section_header", "title") and getattr(item, "text", None):
                headings.append(item.text)
    except Exception:
        pass

    tables = [_to_normalized_table(i, t, dl_doc) for i, t in enumerate(getattr(dl_doc, "tables", []) or [])]
    images = [
        img
        for i, p in enumerate(getattr(dl_doc, "pictures", []) or [])
        if (img := _to_normalized_image(i, p, dl_doc)) is not None
    ]

    page_count = None
    try:
        pages = getattr(dl_doc, "pages", None)
        page_count = len(pages) if pages else None
    except Exception:
        pass

    title, author, creation_date, modified_date = extract_container_metadata(file_path, fmt)
    if not title:
        title = headings[0] if headings else file_path.stem

    return NormalizedDocument(
        text=text,
        tables=tables,
        images=images,
        metadata=DocumentMetadata(
            document_type=fmt.value,
            title=title,
            author=author,
            creation_date=creation_date,
            modified_date=modified_date,
            page_count=page_count,
            headings=headings,
        ),
    )
