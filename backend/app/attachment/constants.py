"""Constants, limits, magic byte signatures, and standardized error codes for Attachments."""

from __future__ import annotations

# Standardized Attachment Validation Error Codes (Invariants R-20 & R-21)
ERR_ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED = "TOTAL_ATTACHMENT_SIZE_EXCEEDED"
ERR_ATTACHMENT_COUNT_EXCEEDED = "ATTACHMENT_COUNT_EXCEEDED"
ERR_UNSUPPORTED_ATTACHMENT_TYPE = "UNSUPPORTED_ATTACHMENT_TYPE"
ERR_ENCRYPTED_OR_PASSWORD_PROTECTED = "ENCRYPTED_OR_PASSWORD_PROTECTED_FILE"  # noqa: S105
ERR_ATTACHMENT_CORRUPT = "ATTACHMENT_CORRUPT"

# 7 Supported MIME types and primary signatures
SUPPORTED_ATTACHMENT_MIMES: dict[str, bytes | None] = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",  # Offset 0 RIFF, Offset 8 WEBP
    "application/pdf": b"%PDF-",
    "text/plain": None,  # Inspected via UTF-8/ASCII heuristic, zero null bytes
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK\x03\x04",
}

# Supported file extensions mapped to canonical MIME types
SUPPORTED_EXTENSIONS_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Executable binary signatures (Malware / PE / ELF / Scripts)
EXECUTABLE_SIGNATURES: list[bytes] = [
    b"MZ",  # Windows DOS/PE Executable
    b"\x7fELF",  # Linux ELF Executable
    b"\xca\xfe\xba\xbe",  # Mach-O Fat Binary / Java Class
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit
]

# Office Macro entry markers inside Zip structures
FORBIDDEN_ZIP_ENTRIES: list[str] = [
    "vbaProject.bin",
    "macrosheets",
    "vbaProject",
]

# Office Compound File Binary Format (CFBF / OLE) header for encrypted Office documents
OLE_CFBF_HEADER: bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OLE_ENCRYPTED_PACKAGE_MARKER: bytes = b"EncryptedPackage"
