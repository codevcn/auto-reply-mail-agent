"""Pydantic schemas for attachment processing and validation."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ParsedEmailPart(BaseModel):
    """In-memory representation of an extracted attachment part before DB persistence.

    INVARIANT R-04: raw_bytes is strictly kept in memory and never written to PostgreSQL.
    """

    filename: str
    content_type: str
    size_bytes: int
    raw_bytes: bytes = Field(repr=False)
    is_inline: bool = False
    content_id: str | None = None
    is_password_protected: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AttachmentMetadataDTO(BaseModel):
    """Safe metadata stored in DB or transmitted to frontend (Zero raw bytes)."""

    id: uuid.UUID | None = None
    filename: str
    content_type: str
    size_bytes: int
    sha256_hash: str
    is_inline: bool = False
    content_id: str | None = None
    is_valid: bool = True
    validation_error: str | None = None
    extracted_text: str | None = None


class AttachmentValidationResult(BaseModel):
    """Overall attachment validation result for an email."""

    is_valid: bool
    error_code: str | None = None
    total_size_bytes: int = 0
    total_count: int = 0
    attachments: list[AttachmentMetadataDTO] = Field(default_factory=list)


class ParsedEmailContent(BaseModel):
    """In-memory email content container parsed from raw RFC 822."""

    subject: str = ""
    sender_email: str = ""
    sender_name: str | None = None
    recipient_email: str = ""
    body_text: str = ""
    body_html_sanitized: str = ""
    attachments: list[ParsedEmailPart] = Field(default_factory=list)
    has_attachments: bool = False
    attachment_count: int = 0

    model_config = ConfigDict(arbitrary_types_allowed=True)
