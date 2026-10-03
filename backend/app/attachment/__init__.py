"""Attachment processing and validation module."""

from app.attachment.constants import (
    ERR_ATTACHMENT_CORRUPT,
    ERR_ATTACHMENT_COUNT_EXCEEDED,
    ERR_ATTACHMENT_TOO_LARGE,
    ERR_ENCRYPTED_OR_PASSWORD_PROTECTED,
    ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED,
    ERR_UNSUPPORTED_ATTACHMENT_TYPE,
    SUPPORTED_ATTACHMENT_MIMES,
)
from app.attachment.parser import MimeEmailParser
from app.attachment.schemas import (
    AttachmentMetadataDTO,
    AttachmentValidationResult,
    ParsedEmailContent,
    ParsedEmailPart,
)
from app.attachment.service import AttachmentService

__all__ = [
    "AttachmentMetadataDTO",
    "AttachmentService",
    "AttachmentValidationResult",
    "ERR_ATTACHMENT_CORRUPT",
    "ERR_ATTACHMENT_COUNT_EXCEEDED",
    "ERR_ATTACHMENT_TOO_LARGE",
    "ERR_ENCRYPTED_OR_PASSWORD_PROTECTED",
    "ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED",
    "ERR_UNSUPPORTED_ATTACHMENT_TYPE",
    "MimeEmailParser",
    "ParsedEmailContent",
    "ParsedEmailPart",
    "SUPPORTED_ATTACHMENT_MIMES",
]
