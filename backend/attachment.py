"""
attachment.py — Durable attachment processing pipeline.

Pipeline stages:
  1. validate_upload   — size + MIME type + extension allowlist
  2. store_original    — content-addressed local filesystem storage
  3. scan_file         — pluggable scanner (noop by default; ClamAV optional)
  4. extract_text      — pypdf for PDFs; pytesseract for images; plaintext passthrough
  5. index_text        — store extracted text in RunAttachment.extracted_text
  6. link_evidence     — append to RunAttachment.evidence_refs
  7. get_citation      — return a citable source reference string

Storage layout:
  <attachment_root>/<sha256[:2]>/<sha256>   (content-addressed; deduplication)

Never send raw binary files to the LLM — only pass extracted text.
"""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BYTES = settings.max_attachment_bytes   # 25 MB

ALLOWED_MIME_PREFIXES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",         # .xlsx
    "application/msword",
    "application/vnd.ms-excel",
}

ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".csv", ".md", ".json", ".xml",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".docx", ".xlsx", ".doc", ".xls",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class AttachmentValidationError(ValueError):
    pass


def validate_upload(filename: str, content_type: str, size: int) -> str:
    """Validate and normalise the MIME type.

    Returns the normalised MIME type string.
    Raises AttachmentValidationError on any violation.
    """
    if size > MAX_BYTES:
        raise AttachmentValidationError(
            f"File size {size:,} bytes exceeds the {MAX_BYTES // 1_000_000} MB limit"
        )
    if size == 0:
        raise AttachmentValidationError("Empty file is not allowed")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentValidationError(
            f"File extension '{ext}' is not allowed. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # Normalise MIME type
    mime = (content_type or "").split(";")[0].strip().lower()
    if not mime:
        # Guess from extension
        guessed, _ = mimetypes.guess_type(filename)
        mime = guessed or "application/octet-stream"

    # Check mime prefix
    if not any(mime.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        raise AttachmentValidationError(
            f"Content-Type '{mime}' is not allowed"
        )

    return mime


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _attachment_root() -> Path:
    root = Path(settings.attachment_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_original(data: bytes, filename: str) -> tuple[str, Path]:
    """Write *data* to content-addressed storage.

    Returns ``(sha256_hex, storage_path)``.
    The file is stored at ``<attachment_root>/<sha256[:2]>/<sha256>``.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    shard = sha256[:2]
    dest_dir = _attachment_root() / shard
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sha256
    if not dest.exists():
        # Write atomically via a temp file
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.rename(dest)
    return sha256, dest


# ---------------------------------------------------------------------------
# Scanning (pluggable)
# ---------------------------------------------------------------------------

class ScanResult:
    CLEAN = "clean"
    MALICIOUS = "malicious"
    ERROR = "error"
    SKIPPED = "skipped"


def scan_file(path: Path) -> tuple[str, str | None]:
    """Scan *path* for malicious content.

    Returns ``(ScanResult constant, detail_message_or_None)``.
    The default implementation is a no-op (skipped).
    Register a custom scanner by monkey-patching ``_active_scanner``.
    """
    return _active_scanner(path)


def _noop_scanner(path: Path) -> tuple[str, str | None]:
    """Default scanner: skip."""
    return ScanResult.SKIPPED, None


# Pluggable scanner hook — replace with ClamAV wrapper when available
_active_scanner = _noop_scanner


def register_scanner(fn) -> None:
    """Register a custom scanner function.
    fn(path: Path) -> (ScanResult, detail: str | None)
    """
    global _active_scanner
    _active_scanner = fn


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(data: bytes, mime: str, filename: str) -> tuple[str, int | None, str | None]:
    """Extract human-readable text from *data*.

    Returns ``(extracted_text, page_count_or_None, error_or_None)``.
    """
    try:
        if mime == "application/pdf" or filename.lower().endswith(".pdf"):
            return _extract_pdf(data)
        if mime.startswith("image/"):
            return _extract_image_ocr(data, filename)
        if mime in ("text/plain", "text/csv", "text/markdown") or filename.endswith(
            (".txt", ".csv", ".md", ".json", ".xml")
        ):
            text = data.decode("utf-8", errors="replace")
            return text, None, None
        # For Office formats we don't have a dep — return a placeholder
        return f"[{filename}: text extraction not available for {mime}]", None, None
    except Exception as exc:
        logger.warning("Text extraction failed for %s: %s", filename, exc)
        return "", None, str(exc)


def _extract_pdf(data: bytes) -> tuple[str, int, str | None]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(f"[Page {i + 1}]\n{text}")
        return "\n\n".join(pages), len(reader.pages), None
    except ImportError:
        return "[PDF extraction requires pypdf]", None, "pypdf not installed"
    except Exception as exc:
        return "", None, str(exc)


def _extract_image_ocr(data: bytes, filename: str) -> tuple[str, None, str | None]:
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(img)
        return text, None, None
    except ImportError:
        return f"[Image OCR requires pytesseract + Pillow; install with pip install pytesseract pillow]", None, "pytesseract not installed"
    except Exception as exc:
        return "", None, str(exc)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def process_attachment(
    db,
    run_id: str,
    project_id: int,
    uploader_id: int,
    filename: str,
    content_type: str,
    data: bytes,
) -> "models.RunAttachment":
    """Run the full attachment processing pipeline and persist the result.

    Returns the persisted RunAttachment ORM object.
    Raises AttachmentValidationError for invalid uploads.
    """
    import models  # local import to avoid circular

    # 1. Validate
    mime = validate_upload(filename, content_type, len(data))

    # 2. Store
    sha256, storage_path = store_original(data, filename)

    # 3. Create record
    now = datetime.now(timezone.utc)
    attachment = models.RunAttachment(
        run_id=run_id,
        project_id=project_id,
        uploader_id=uploader_id,
        original_filename=filename,
        mime_type=mime,
        size_bytes=len(data),
        content_hash=sha256,
        storage_path=str(storage_path),
        storage_backend="local",
        processing_state="scanning",
        created_at=now,
    )
    db.add(attachment)
    db.flush()

    # 3. Scan
    scan_result, scan_detail = scan_file(storage_path)
    attachment.scan_result = scan_result

    if scan_result == ScanResult.MALICIOUS:
        attachment.processing_state = "failed"
        attachment.extraction_error = f"Malicious content detected: {scan_detail}"
        db.commit()
        raise AttachmentValidationError(
            f"File '{filename}' was rejected by the security scanner"
        )

    # 4. Extract text
    attachment.processing_state = "extracting"
    db.flush()

    extracted, page_count, error = extract_text(data, mime, filename)
    attachment.extracted_text = extracted[:500_000]  # cap at 500 kB
    attachment.page_count = page_count
    attachment.extraction_error = error

    # 5. Mark done
    attachment.processing_state = "indexed" if not error else "failed"
    attachment.processed_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "Attachment processed: id=%s filename=%s state=%s pages=%s",
        attachment.id, filename, attachment.processing_state, page_count,
    )
    return attachment


# ---------------------------------------------------------------------------
# Evidence linking + citation
# ---------------------------------------------------------------------------

def link_evidence(
    db,
    attachment_id: str,
    run_id: str,
    artifact_id: str | None = None,
    page: int | None = None,
) -> None:
    """Append an evidence reference to a RunAttachment."""
    import models
    attachment = db.get(models.RunAttachment, attachment_id)
    if not attachment:
        return
    refs = list(attachment.evidence_refs or [])
    refs.append({
        "run_id": run_id,
        "artifact_id": artifact_id,
        "page": page,
        "linked_at": datetime.now(timezone.utc).isoformat(),
    })
    attachment.evidence_refs = refs
    db.commit()


def get_citation(db, attachment_id: str, page: int | None = None) -> str:
    """Return a citable source reference string for use in agent responses."""
    import models
    attachment = db.get(models.RunAttachment, attachment_id)
    if not attachment:
        return f"[attachment:{attachment_id}]"
    name = attachment.original_filename
    if page is not None:
        return f"[{name}, page {page}]"
    if attachment.page_count and attachment.page_count > 1:
        return f"[{name}, {attachment.page_count} pages]"
    return f"[{name}]"


def get_extracted_text_for_agent(db, attachment_id: str, max_chars: int = 50_000) -> str:
    """Return extracted text suitable for inclusion in agent context.

    Includes a citation header so the agent can reference the source.
    """
    import models
    attachment = db.get(models.RunAttachment, attachment_id)
    if not attachment:
        return f"[Attachment {attachment_id} not found]"
    if attachment.processing_state not in ("indexed", "clean"):
        return f"[Attachment '{attachment.original_filename}' is still processing ({attachment.processing_state})]"
    if not attachment.extracted_text:
        return f"[Attachment '{attachment.original_filename}' has no extractable text]"
    citation = get_citation(db, attachment_id)
    text = attachment.extracted_text[:max_chars]
    truncated = len(attachment.extracted_text) > max_chars
    suffix = "\n[... truncated]" if truncated else ""
    return f"--- Source: {citation} ---\n{text}{suffix}"
