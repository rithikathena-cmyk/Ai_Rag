"""services/ingestion/storage.py — sanitize_filename()/save_original() must
never let a client-supplied filename write outside the document's own
"original" directory. Found during the guardrails audit: file.filename (an
attacker-controlled multipart field, routers/documents.py's upload_document)
was joined into a filesystem path with zero sanitization — a value like
"../../evil.txt" or an absolute path would have escaped doc_dir entirely,
since Path.__truediv__ does not normalize ".." and silently discards the
left side when the right side is itself absolute.
"""

import uuid

import pytest

from app.services.ingestion import storage


@pytest.fixture
def doc_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    return storage.make_document_dir(uuid.uuid4())


def test_ordinary_filename_is_unchanged():
    assert storage.sanitize_filename("report.pdf") == "report.pdf"


def test_filename_with_spaces_and_unicode_is_preserved():
    assert storage.sanitize_filename("Q2 Résumé.docx") == "Q2 Résumé.docx"


@pytest.mark.parametrize(
    "malicious,expected_name",
    [
        ("../../../etc/passwd", "passwd"),
        ("..\\..\\..\\Windows\\System32\\evil.dll", "evil.dll"),
        ("/etc/passwd", "passwd"),
        ("C:\\Windows\\evil.dll", "evil.dll"),
        ("....//....//etc/passwd", "passwd"),
    ],
)
def test_path_traversal_attempts_are_reduced_to_a_bare_filename(malicious, expected_name):
    assert storage.sanitize_filename(malicious) == expected_name


def test_windows_reserved_characters_are_stripped():
    assert storage.sanitize_filename('bad<>:"|?*name.txt') == "badname.txt"


def test_null_byte_is_stripped():
    assert "\x00" not in storage.sanitize_filename("evil\x00.txt.pdf")


def test_empty_or_dot_only_filename_falls_back_to_a_safe_default():
    assert storage.sanitize_filename("") == "unnamed"
    assert storage.sanitize_filename("...") == "unnamed"
    assert storage.sanitize_filename("../../..") == "unnamed"


def test_save_original_writes_inside_doc_dir_even_with_a_traversal_filename(doc_dir):
    path = storage.save_original(doc_dir, "../../../etc/passwd", b"malicious content")
    assert path.parent == (doc_dir / "original").resolve()
    assert path.read_bytes() == b"malicious content"


def test_save_original_writes_inside_doc_dir_with_an_absolute_windows_path(doc_dir):
    path = storage.save_original(doc_dir, "C:\\Windows\\System32\\evil.dll", b"x")
    assert path.parent == (doc_dir / "original").resolve()


def test_save_original_normal_filename_still_works(doc_dir):
    path = storage.save_original(doc_dir, "report.pdf", b"pdf bytes")
    assert path.name == "report.pdf"
    assert path.read_bytes() == b"pdf bytes"
