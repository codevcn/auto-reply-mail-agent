"""Pure-Python file signature and magic bytes sniffing engine (Zero OS C-lib dependencies)."""

from __future__ import annotations

import io
import os
import zipfile

from app.attachment.constants import (
    ERR_ATTACHMENT_CORRUPT,
    ERR_UNSUPPORTED_ATTACHMENT_TYPE,
    EXECUTABLE_SIGNATURES,
    FORBIDDEN_ZIP_ENTRIES,
    SUPPORTED_ATTACHMENT_MIMES,
)


def is_executable_binary(data: bytes) -> bool:
    """Checks if binary payload matches known executable or library formats."""
    return any(data.startswith(sig) for sig in EXECUTABLE_SIGNATURES)



def is_plain_text(data: bytes) -> bool:
    """Evaluates whether raw bytes represent valid text without binary control codes or executables."""
    if not data:
        return True

    # 1. Reject binary executables masquerading as text
    if is_executable_binary(data):
        return False

    # 2. Reject shell scripts with shebang
    if data.startswith(b"#!/"):
        return False

    # 3. Reject null bytes (fundamental indicator of binary streams)
    if b"\x00" in data:
        return False

    # 4. Attempt clean text decoding across standard character sets
    decoded = False
    for encoding in ("utf-8", "ascii", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            decoded = True
            # Check ratio of non-printable control characters
            control_chars = sum(
                1 for c in text if ord(c) < 32 and c not in ("\t", "\n", "\r")
            )
            if len(text) > 0 and (control_chars / len(text)) > 0.05:
                return False
            break
        except UnicodeDecodeError:
            continue

    return decoded


def sniff_mime_type(
    data: bytes,
    filename: str | None = None,
    declared_mime: str | None = None,
) -> tuple[str | None, str | None]:
    """Sniffs and validates true MIME type from raw bytes.

    Returns:
        tuple[detected_mime_or_none, error_code_or_none]
    """
    if len(data) == 0:
        return None, ERR_ATTACHMENT_CORRUPT

    # 1. Early security check: Reject dangerous executables
    if is_executable_binary(data):
        return None, ERR_UNSUPPORTED_ATTACHMENT_TYPE

    ext = ""
    if filename:
        _, ext = os.path.splitext(filename.lower())

    # 2. JPEG: SOI marker FF D8 FF
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", None

    # 3. PNG: 8-byte signature 89 50 4E 47 0D 0A 1A 0A
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", None

    # 4. WebP: RIFF at 0 and WEBP at 8
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp", None

    # 5. PDF: %PDF- in the first 1024 bytes (per PDF specification RFC 3778 / ISO 32000-1)
    if b"%PDF-" in data[:1024]:
        # Validate that PDF has minimum structural viability
        if len(data) < 16:
            return None, ERR_ATTACHMENT_CORRUPT
        return "application/pdf", None

    # 6. OpenXML Office documents (DOCX & XLSX): PK\x03\x04 zip container
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                namelist = zf.namelist()

                # Security check: detect malicious macros (docm/xlsm disguised as docx/xlsx)
                for entry in namelist:
                    entry_lower = entry.lower()
                    for forbidden in FORBIDDEN_ZIP_ENTRIES:
                        if forbidden.lower() in entry_lower:
                            return None, ERR_UNSUPPORTED_ATTACHMENT_TYPE

                # Check if it has [Content_Types].xml (standard OpenXML package)
                has_content_types = any(
                    name.endswith("[Content_Types].xml") for name in namelist
                )

                # Differentiate DOCX vs XLSX
                has_word = any(name.startswith("word/") for name in namelist)
                has_xl = any(name.startswith("xl/") for name in namelist)

                if has_word and (has_content_types or ext == ".docx"):
                    return (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        None,
                    )
                if has_xl and (has_content_types or ext == ".xlsx"):
                    return (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        None,
                    )

                # Fallback based on extension or declared MIME if valid OpenXML
                if ext == ".docx" or declared_mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                    return (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        None,
                    )
                if ext == ".xlsx" or declared_mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                    return (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        None,
                    )

                # Generic or unknown zip file -> unsupported in V1
                return None, ERR_UNSUPPORTED_ATTACHMENT_TYPE
        except zipfile.BadZipFile:
            return None, ERR_ATTACHMENT_CORRUPT
        except Exception:
            return None, ERR_ATTACHMENT_CORRUPT

    # 7. Plain Text
    if is_plain_text(data) and (ext in (".txt", "") or declared_mime in ("text/plain", None)):
        return "text/plain", None


    # If declared mime is one of the supported ones, but bytes don't match -> spoofed or corrupt
    if declared_mime in SUPPORTED_ATTACHMENT_MIMES:
        expected_sig = SUPPORTED_ATTACHMENT_MIMES.get(declared_mime)
        if expected_sig and not data.startswith(expected_sig):
            return None, ERR_UNSUPPORTED_ATTACHMENT_TYPE

    # Any other unrecognized file type
    return None, ERR_UNSUPPORTED_ATTACHMENT_TYPE
