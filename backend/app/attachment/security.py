"""Security inspection routines: encryption/password checks, filename sanitization, and safe text extraction."""

from __future__ import annotations

import io
import os
import re
import zipfile

from app.attachment.constants import (
    OLE_CFBF_HEADER,
    OLE_ENCRYPTED_PACKAGE_MARKER,
)


def is_encrypted_or_password_protected(data: bytes) -> bool:
    """Detects whether an attachment is encrypted or password-protected."""
    if not data:
        return False

    # 1. PDF password encryption check
    if b"%PDF-" in data[:1024] and (b"/Encrypt" in data or b"/Crypt" in data):
        return True

    # 2. Microsoft Office EncryptedPackage (OLE CFBF format)
    # Password-protected DOCX/XLSX are encapsulated in Compound File Binary Format
    if data.startswith(OLE_CFBF_HEADER) and OLE_ENCRYPTED_PACKAGE_MARKER in data:
        return True

    # 3. Standard Zip encryption (bit 0 of general purpose bit flag is set)
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for zinfo in zf.infolist():
                    # Check general purpose bit flag: bit 0 indicates encryption
                    if zinfo.flag_bits & 0x1:
                        return True
        except Exception:  # noqa: S110
            pass

    return False


def sanitize_filename(
    raw_filename: str | None,
    default_index: int = 1,
    fallback_ext: str = ".bin",
) -> str:
    """Sanitizes filename against path traversal and special characters."""
    if not raw_filename or not raw_filename.strip():
        return f"attachment_{default_index}{fallback_ext}"

    # Extract pure basename to thwart directory traversal (../../, C:\Windows, etc.)
    clean_name = os.path.basename(raw_filename.strip())

    # Replace forbidden Windows and POSIX characters with underscore
    clean_name = re.sub(r'[\x00-\x1f\\/:*?"<>|]', "_", clean_name)

    # Trim leading and trailing periods/spaces
    clean_name = clean_name.strip(". ")
    if not clean_name:
        return f"attachment_{default_index}{fallback_ext}"

    # Enforce maximum length of 255 characters
    if len(clean_name) > 255:
        name_part, ext_part = os.path.splitext(clean_name)
        keep_len = 255 - len(ext_part)
        clean_name = name_part[:keep_len] + ext_part

    return clean_name


def extract_safe_text(data: bytes, mime_type: str) -> str | None:
    """Extracts plain text content safely in-memory for AI context without running macros."""
    if not data:
        return None

    if mime_type == "text/plain":
        for encoding in ("utf-8", "ascii", "cp1252", "latin-1"):
            try:
                return data.decode(encoding, errors="replace").strip()
            except Exception:  # noqa: S112
                continue
        return None

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    raw_xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
                    clean_text = re.sub(r"<[^>]+>", " ", raw_xml)
                    return " ".join(clean_text.split()).strip()
        except Exception:
            return None

    if mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                texts = []
                for entry in ("xl/sharedStrings.xml", "xl/workbook.xml"):
                    if entry in zf.namelist():
                        raw_xml = zf.read(entry).decode("utf-8", errors="replace")
                        clean_text = re.sub(r"<[^>]+>", " ", raw_xml)
                        texts.append(" ".join(clean_text.split()))
                if texts:
                    return " ".join(texts).strip()
        except Exception:
            return None

    return None


