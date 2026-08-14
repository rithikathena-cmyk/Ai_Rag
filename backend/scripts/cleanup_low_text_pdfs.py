"""Finds PDFs ingested before the OCR-fallback fix (services/ingestion/
dispatcher.py) that yielded near-zero extractable text — scanned/image-only
PDFs that went through PyMuPDF-only parsing and ended up effectively
unsearchable — and, on request, deletes them (Postgres + Qdrant + on-disk
files, via the same delete_document_row() the DELETE /documents/{id}
endpoint uses) so they can be re-uploaded through the new OCR-fallback path.

Usage (from backend/, with Postgres and Qdrant reachable):
    python -m scripts.cleanup_low_text_pdfs                # list candidates only
    python -m scripts.cleanup_low_text_pdfs --delete        # list, then delete them
    python -m scripts.cleanup_low_text_pdfs --include-old-versions --delete

Uses the same chars-per-page threshold as the ingestion-time fallback
(settings.pdf_ocr_fallback_min_chars_per_page) so "would this doc trigger
OCR today" and "does this doc need cleanup" stay in sync.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.postgres import new_session  # noqa: E402
from app.models.document import DocumentModel  # noqa: E402
from app.routers.documents import delete_document_row  # noqa: E402


def _chars_per_page(row: DocumentModel) -> float:
    if not row.text_file_path:
        return 0.0
    try:
        text = Path(row.text_file_path).read_text(encoding="utf-8")
    except OSError:
        return 0.0
    return len(text.strip()) / (row.page_count or 1)


def find_candidates(db: Session, *, include_old_versions: bool) -> list[tuple[DocumentModel, float]]:
    query = db.query(DocumentModel).filter(DocumentModel.document_type == "pdf")
    if not include_old_versions:
        query = query.filter(DocumentModel.is_latest_version.is_(True))

    candidates = []
    for row in query.all():
        chars_per_page = _chars_per_page(row)
        if chars_per_page < settings.pdf_ocr_fallback_min_chars_per_page:
            candidates.append((row, chars_per_page))
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--delete", action="store_true",
        help="Delete the candidates found (Postgres + Qdrant + on-disk files). Without this flag, only lists them.",
    )
    parser.add_argument(
        "--include-old-versions", action="store_true",
        help="Also check superseded (non-latest) document versions, not just the current one.",
    )
    args = parser.parse_args()

    db = new_session()
    try:
        candidates = find_candidates(db, include_old_versions=args.include_old_versions)
        if not candidates:
            print("No low-text PDFs found.")
            return

        print(
            f"{len(candidates)} low-text PDF(s) found "
            f"(threshold: {settings.pdf_ocr_fallback_min_chars_per_page:.0f} chars/page):\n"
        )
        for row, chars_per_page in candidates:
            print(
                f"  {row.id}  {row.filename!r}  {chars_per_page:.1f} chars/page over "
                f"{row.page_count or '?'} page(s)  (latest={row.is_latest_version})"
            )

        if not args.delete:
            print("\nDry run — pass --delete to remove these from Postgres + Qdrant so they can be re-uploaded.")
            return

        print()
        for row, _ in candidates:
            print(f"Deleting {row.id} ({row.filename!r})...")
            delete_document_row(db, row.id)
        print(f"\nDeleted {len(candidates)} document(s). Re-upload them to reprocess through the OCR-fallback path.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
