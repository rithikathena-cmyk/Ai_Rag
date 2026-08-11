from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.upload_validation import validate_mime


def test_pdf_signature_matches_pdf_format():
    assert validate_mime(DocumentFormat.PDF, b"%PDF-1.7\n...")


def test_pdf_signature_rejects_other_formats():
    assert not validate_mime(DocumentFormat.IMAGE, b"%PDF-1.7\n...")


def test_png_signature_matches_image_format():
    assert validate_mime(DocumentFormat.IMAGE, b"\x89PNG\r\n\x1a\n\x00\x00")


def test_docx_zip_signature_matches_office_formats():
    zip_bytes = b"PK\x03\x04" + b"\x00" * 20
    assert validate_mime(DocumentFormat.DOCX, zip_bytes)
    assert validate_mime(DocumentFormat.PPTX, zip_bytes)
    assert validate_mime(DocumentFormat.XLSX, zip_bytes)


def test_mismatched_binary_content_is_rejected():
    # A PNG renamed to .pdf: extension says PDF, bytes say PNG.
    assert not validate_mime(DocumentFormat.PDF, b"\x89PNG\r\n\x1a\n\x00\x00")


def test_unrecognized_binary_content_is_rejected():
    assert not validate_mime(DocumentFormat.PDF, b"random garbage bytes")


def test_text_formats_pass_without_a_signature():
    for fmt in (
        DocumentFormat.TXT,
        DocumentFormat.MARKDOWN,
        DocumentFormat.CSV,
        DocumentFormat.JSON,
        DocumentFormat.CODE,
    ):
        assert validate_mime(fmt, b"whatever text content, no magic bytes needed")
