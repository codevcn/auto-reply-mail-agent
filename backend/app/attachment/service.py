"""Attachment Service: Orchestrates validation, sniffing, and safe metadata persistence.

INVARIANT R-04: ZERO raw bytes are written to PostgreSQL.
INVARIANTS R-20 & R-21: Strict limits, fail-closed validation, and error routing.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.attachment.constants import (
    ERR_ATTACHMENT_CORRUPT,
    ERR_ATTACHMENT_COUNT_EXCEEDED,
    ERR_ATTACHMENT_TOO_LARGE,
    ERR_ENCRYPTED_OR_PASSWORD_PROTECTED,
    ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED,
)
from app.attachment.parser import MimeEmailParser
from app.attachment.schemas import (
    AttachmentMetadataDTO,
    AttachmentValidationResult,
    ParsedEmailContent,
    ParsedEmailPart,
)
from app.attachment.security import (
    extract_safe_text,
    is_encrypted_or_password_protected,
)
from app.attachment.sniffing import sniff_mime_type
from app.db.models.email import EmailAttachmentMetadata


class AttachmentService:
    """Provides attachment parsing, sniffing, security inspection, and metadata recording."""

    @staticmethod
    def validate_attachments(
        attachments: list[ParsedEmailPart],
        max_single_bytes: int = 10 * 1024 * 1024,
        max_total_bytes: int = 25 * 1024 * 1024,
        max_count: int = 10,
    ) -> AttachmentValidationResult:
        """Validates all attachments against size limits, magic bytes, and security constraints."""
        total_count = len(attachments)
        total_size = sum(att.size_bytes for att in attachments)

        # 1. Check attachment count limit
        if total_count > max_count:
            return AttachmentValidationResult(
                is_valid=False,
                error_code=ERR_ATTACHMENT_COUNT_EXCEEDED,
                total_size_bytes=total_size,
                total_count=total_count,
            )

        # 2. Check total attachment size limit
        if total_size > max_total_bytes:
            return AttachmentValidationResult(
                is_valid=False,
                error_code=ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED,
                total_size_bytes=total_size,
                total_count=total_count,
            )

        dtos: list[AttachmentMetadataDTO] = []
        overall_valid = True
        first_error_code: str | None = None

        # 3. Check individual attachments
        for att in attachments:
            sha256 = hashlib.sha256(att.raw_bytes).hexdigest()
            error_code: str | None = None

            # Check single size limit
            if att.size_bytes > max_single_bytes:
                error_code = ERR_ATTACHMENT_TOO_LARGE

            # Check empty file
            elif att.size_bytes == 0:
                error_code = ERR_ATTACHMENT_CORRUPT

            # Check password / encryption
            elif att.is_password_protected or is_encrypted_or_password_protected(att.raw_bytes):
                error_code = ERR_ENCRYPTED_OR_PASSWORD_PROTECTED

            else:
                # Magic bytes sniffing
                detected_mime, sniff_err = sniff_mime_type(
                    att.raw_bytes,
                    filename=att.filename,
                    declared_mime=att.content_type,
                )
                if sniff_err:
                    error_code = sniff_err
                else:
                    att.content_type = detected_mime or att.content_type

            # Safe text extraction if valid
            extracted_text: str | None = None
            if not error_code:
                extracted_text = extract_safe_text(att.raw_bytes, att.content_type)

            if error_code and overall_valid:
                overall_valid = False
                first_error_code = error_code

            dtos.append(
                AttachmentMetadataDTO(
                    filename=att.filename,
                    content_type=att.content_type,
                    size_bytes=att.size_bytes,
                    sha256_hash=sha256,
                    is_inline=att.is_inline,
                    content_id=att.content_id,
                    is_valid=error_code is None,
                    validation_error=error_code,
                    extracted_text=extracted_text,
                )
            )

        return AttachmentValidationResult(
            is_valid=overall_valid,
            error_code=first_error_code,
            total_size_bytes=total_size,
            total_count=total_count,
            attachments=dtos,
        )

    @staticmethod
    def parse_and_validate(
        raw_email_bytes: bytes,
        max_single_bytes: int = 10 * 1024 * 1024,
        max_total_bytes: int = 25 * 1024 * 1024,
        max_count: int = 10,
    ) -> tuple[ParsedEmailContent, AttachmentValidationResult]:
        """Parses email and validates all attachments completely in-memory."""
        parsed_email = MimeEmailParser.parse_raw_message(raw_email_bytes)
        val_result = AttachmentService.validate_attachments(
            parsed_email.attachments,
            max_single_bytes=max_single_bytes,
            max_total_bytes=max_total_bytes,
            max_count=max_count,
        )
        return parsed_email, val_result

    @staticmethod
    async def persist_attachment_metadata(
        session: AsyncSession,
        incoming_email_id: uuid.UUID,
        validation_result: AttachmentValidationResult,
    ) -> list[EmailAttachmentMetadata]:
        """Persists safe metadata to PostgreSQL (ZERO raw bytes)."""
        records: list[EmailAttachmentMetadata] = []
        for dto in validation_result.attachments:
            rec = EmailAttachmentMetadata(
                id=uuid.uuid4(),
                incoming_email_id=incoming_email_id,
                filename=dto.filename,
                content_type=dto.content_type,
                size_bytes=dto.size_bytes,
                sha256_hash=dto.sha256_hash,
                is_inline=dto.is_inline,
                content_id=dto.content_id,
                is_valid=dto.is_valid,
                validation_error=dto.validation_error,
            )
            session.add(rec)
            records.append(rec)
        await session.commit()
        return records
