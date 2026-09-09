"""
tests/test_attachment_pipeline.py — Durable attachment pipeline unit tests.
"""

from __future__ import annotations

import pytest
import io
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from attachment import (
    validate_upload,
    store_original,
    extract_text,
    scan_file,
    get_citation,
    get_extracted_text_for_agent,
    AttachmentValidationError,
    ScanResult,
    MAX_BYTES,
    _noop_scanner,
    register_scanner,
    _active_scanner,
)


# ---------------------------------------------------------------------------
# validate_upload
# ---------------------------------------------------------------------------

def test_validate_pdf_ok():
    mime = validate_upload("report.pdf", "application/pdf", 1_000)
    assert mime == "application/pdf"


def test_validate_txt_ok():
    mime = validate_upload("notes.txt", "text/plain", 500)
    assert "text" in mime


def test_validate_image_ok():
    mime = validate_upload("screenshot.png", "image/png", 2_000_000)
    assert mime == "image/png"


def test_validate_empty_file_rejected():
    with pytest.raises(AttachmentValidationError, match="Empty file"):
        validate_upload("file.pdf", "application/pdf", 0)


def test_validate_too_large_rejected():
    with pytest.raises(AttachmentValidationError, match="exceeds"):
        validate_upload("large.pdf", "application/pdf", MAX_BYTES + 1)


def test_validate_bad_extension_rejected():
    with pytest.raises(AttachmentValidationError, match="extension"):
        validate_upload("malware.exe", "application/octet-stream", 100)


def test_validate_bad_mime_rejected():
    with pytest.raises(AttachmentValidationError, match="Content-Type"):
        validate_upload("file.pdf", "application/x-executable", 100)


def test_validate_guesses_mime_from_extension():
    # content_type empty → should guess from extension
    mime = validate_upload("data.json", "", 200)
    assert "json" in mime or "javascript" in mime or "text" in mime


def test_validate_docx_ok():
    mime = validate_upload(
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        50_000,
    )
    assert "docx" in mime or "openxmlformats" in mime or "word" in mime.lower()


# ---------------------------------------------------------------------------
# store_original
# ---------------------------------------------------------------------------

def test_store_original_content_addressed(tmp_path, monkeypatch):
    monkeypatch.setattr("attachment.settings.attachment_root", tmp_path)
    data = b"hello world"
    sha256 = hashlib.sha256(data).hexdigest()

    returned_sha, path = store_original(data, "test.txt")
    assert returned_sha == sha256
    assert path.exists()
    assert path.read_bytes() == data


def test_store_original_deduplication(tmp_path, monkeypatch):
    monkeypatch.setattr("attachment.settings.attachment_root", tmp_path)
    data = b"duplicate content"
    sha1, path1 = store_original(data, "a.txt")
    sha2, path2 = store_original(data, "b.txt")
    assert sha1 == sha2
    assert path1 == path2  # Same content → same storage path


def test_store_original_different_content_different_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("attachment.settings.attachment_root", tmp_path)
    sha1, path1 = store_original(b"content A", "a.txt")
    sha2, path2 = store_original(b"content B", "b.txt")
    assert sha1 != sha2
    assert path1 != path2


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------

def test_extract_plaintext():
    data = b"Hello, world!\nThis is a test."
    text, pages, error = extract_text(data, "text/plain", "test.txt")
    assert "Hello, world!" in text
    assert pages is None
    assert error is None


def test_extract_json():
    data = b'{"key": "value", "count": 42}'
    text, pages, error = extract_text(data, "application/json", "data.json")
    assert "value" in text
    assert error is None


def test_extract_pdf_no_pypdf():
    """When pypdf raises ImportError, returns a placeholder."""
    with patch("attachment._extract_pdf") as mock_pdf:
        mock_pdf.return_value = ("[PDF extraction requires pypdf]", None, "pypdf not installed")
        text, pages, error = extract_text(b"%PDF fake", "application/pdf", "test.pdf")
        assert "pypdf" in text or pages is None


def test_extract_image_no_tesseract():
    """When pytesseract isn't installed, returns a placeholder."""
    data = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes (incomplete)
    with patch("attachment._extract_image_ocr") as mock_ocr:
        mock_ocr.return_value = ("[Image OCR requires pytesseract]", None, "pytesseract not installed")
        text, pages, error = extract_text(data, "image/png", "screenshot.png")
        assert "pytesseract" in text


def test_extract_utf8_error_handling():
    """Latin-1 encoded text should not crash extraction."""
    data = b"Caf\xe9 au lait"  # Invalid UTF-8 but valid Latin-1
    text, pages, error = extract_text(data, "text/plain", "test.txt")
    assert "Caf" in text
    assert error is None


# ---------------------------------------------------------------------------
# Scanning (noop)
# ---------------------------------------------------------------------------

def test_noop_scanner_returns_skipped(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"safe content")
    result, detail = _noop_scanner(f)
    assert result == ScanResult.SKIPPED
    assert detail is None


def test_register_scanner():
    def my_scanner(path):
        return ScanResult.CLEAN, None

    original = _active_scanner  # save
    try:
        register_scanner(my_scanner)
        tmp = Path("/tmp/test_scan.txt")
        tmp.write_bytes(b"test")
        result, detail = scan_file(tmp)
        assert result == ScanResult.CLEAN
    finally:
        register_scanner(_noop_scanner)


# ---------------------------------------------------------------------------
# get_citation
# ---------------------------------------------------------------------------

def test_get_citation_single_page():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "report.pdf"
    attachment.page_count = 1
    db.get.return_value = attachment
    citation = get_citation(db, "att-1")
    assert "report.pdf" in citation


def test_get_citation_multi_page():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "long_report.pdf"
    attachment.page_count = 42
    db.get.return_value = attachment
    citation = get_citation(db, "att-1")
    assert "42 pages" in citation


def test_get_citation_with_page():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "reference.pdf"
    attachment.page_count = 10
    db.get.return_value = attachment
    citation = get_citation(db, "att-1", page=5)
    assert "page 5" in citation


def test_get_citation_not_found():
    db = MagicMock()
    db.get.return_value = None
    citation = get_citation(db, "nonexistent")
    assert "att" in citation.lower() or "nonexistent" in citation


# ---------------------------------------------------------------------------
# get_extracted_text_for_agent
# ---------------------------------------------------------------------------

def test_get_extracted_text_indexed():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "spec.pdf"
    attachment.processing_state = "indexed"
    attachment.extracted_text = "This is the extracted content."
    attachment.page_count = 1
    db.get.return_value = attachment
    text = get_extracted_text_for_agent(db, "att-1")
    assert "Source:" in text
    assert "This is the extracted content." in text


def test_get_extracted_text_still_processing():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "report.pdf"
    attachment.processing_state = "scanning"
    db.get.return_value = attachment
    text = get_extracted_text_for_agent(db, "att-1")
    assert "processing" in text.lower() or "scanning" in text.lower()


def test_get_extracted_text_truncated():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "big.pdf"
    attachment.processing_state = "indexed"
    attachment.extracted_text = "A" * 200_000
    attachment.page_count = 50
    db.get.return_value = attachment
    text = get_extracted_text_for_agent(db, "att-1", max_chars=100)
    assert "truncated" in text.lower()
    assert len(text) < 500  # Much less than 200k


def test_get_extracted_text_no_text():
    db = MagicMock()
    attachment = MagicMock()
    attachment.original_filename = "blank.pdf"
    attachment.processing_state = "indexed"
    attachment.extracted_text = None
    db.get.return_value = attachment
    text = get_extracted_text_for_agent(db, "att-1")
    assert "no extractable text" in text.lower()
