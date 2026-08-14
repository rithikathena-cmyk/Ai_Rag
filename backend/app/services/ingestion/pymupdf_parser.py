"""PDF parsing via PyMuPDF: pure C-based text/table/image extraction, the
fast-path parser tried first for every PDF. Has no OCR — dispatcher.py falls
back to Docling's OCR pipeline when this comes back with near-zero text
(the signature of a scanned/image-only PDF)."""

from collections import Counter
from pathlib import Path

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.docling_parser import extract_container_metadata
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument, NormalizedImage, NormalizedTable

_BOLD_FLAG = 1 << 4  # PyMuPDF span.flags bit 4


def _dominant_font_size(pages_dicts: list[dict]) -> float:
    weight_by_size: Counter[float] = Counter()
    for d in pages_dicts:
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        weight_by_size[round(span["size"], 1)] += len(text)
    return weight_by_size.most_common(1)[0][0] if weight_by_size else 10.0


def _is_heading_span(span: dict, body_size: float) -> bool:
    text = span.get("text", "").strip()
    if not text or len(text) > 120:
        return False
    if span["size"] >= body_size * 1.15:
        return True
    # A line that's merely bold at body size (not larger) used to count as a
    # heading on its own, which false-positives badly on dense, bold-heavy
    # layouts like resumes: job titles ("Front End Developer"), company
    # names, and contact-field labels ("Email", "LinkedIn") are commonly
    # bold at body size there, and each one becoming its own heading split a
    # single job entry into disconnected one-line "sections" (the title
    # separated from its own bullets), fragmenting the document into dozens
    # of near-empty chunks. Requiring ALL CAPS as well keeps real
    # same-size-bold section titles ("SUMMARY", "EXPERIENCE") working — the
    # conventional styling for those — while excluding bold body content
    # that merely happens to share the section-title's font weight.
    is_bold = bool(span.get("flags", 0) & _BOLD_FLAG)
    return is_bold and text.isupper()


def _extract_text_with_headings(doc) -> tuple[str, list[str]]:
    # page.get_text() alone loses all structure, which would silently defeat
    # the heading-aware chunking fix (text_utils.split_sections expects '#'
    # markers) — so headings are reconstructed here via a font-size heuristic:
    # a line whose single span is notably larger than the page's dominant
    # (most character-weighted) body text size, or bold at body size.
    pages_dicts = [doc[i].get_text("dict") for i in range(doc.page_count)]
    body_size = _dominant_font_size(pages_dicts)

    lines_out: list[str] = []
    headings: list[str] = []
    for d in pages_dicts:
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if not line_text:
                    continue
                if len(spans) == 1 and _is_heading_span(spans[0], body_size):
                    lines_out.append(f"\n## {line_text}\n")
                    headings.append(line_text)
                else:
                    lines_out.append(line_text)
    return "\n".join(lines_out), headings


def _extract_tables(doc) -> list[NormalizedTable]:
    tables: list[NormalizedTable] = []
    index = 0
    for page_num in range(doc.page_count):
        try:
            found = doc[page_num].find_tables()
        except Exception:
            continue
        for t in found.tables:
            try:
                rows = t.extract()
            except Exception:
                continue
            if not rows:
                continue
            headers = [str(c) if c is not None else "" for c in rows[0]]
            data_rows = [[str(c) if c is not None else "" for c in r] for r in rows[1:]]
            tables.append(NormalizedTable(index=index, headers=headers, rows=data_rows, page=page_num))
            index += 1
    return tables


def _extract_images(doc) -> list[NormalizedImage]:
    images: list[NormalizedImage] = []
    index = 0
    seen_xrefs: set[int] = set()
    for page_num in range(doc.page_count):
        for img in doc[page_num].get_images(full=True):
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                info = doc.extract_image(xref)
                images.append(NormalizedImage(index=index, data=info["image"], format=info["ext"], page=page_num))
                index += 1
            except Exception:
                continue
    return images


def parse(file_path: Path) -> NormalizedDocument:
    import fitz

    with fitz.open(str(file_path)) as doc:
        text, headings = _extract_text_with_headings(doc)
        tables = _extract_tables(doc)
        images = _extract_images(doc)
        page_count = doc.page_count

    # Reuses docling_parser's existing pypdf-based title/author/date
    # extraction — it's already independent of the conversion pipeline, so
    # there's no reason to duplicate it here.
    title, author, creation_date, modified_date = extract_container_metadata(file_path, DocumentFormat.PDF)
    if not title:
        title = headings[0] if headings else file_path.stem

    return NormalizedDocument(
        text=text,
        tables=tables,
        images=images,
        metadata=DocumentMetadata(
            document_type=DocumentFormat.PDF.value,
            title=title,
            author=author,
            creation_date=creation_date,
            modified_date=modified_date,
            page_count=page_count,
            headings=headings,
        ),
    )
