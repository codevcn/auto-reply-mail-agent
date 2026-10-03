"""Attachment router for on-demand downloading of attachments via IMAP PEEK.

INVARIANT R-04: Attachments are fetched directly from the mailserver on demand
and never permanently stored in PostgreSQL.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.attachment.parser import MimeEmailParser
from app.auth.dependencies import require_permission
from app.core.crypto import decrypt_secret
from app.db.models.email import EmailAttachmentMetadata, IncomingEmail
from app.db.models.store import Mailbox
from app.db.session import get_db
from app.mail.imap_client import IMAPClient

attachment_router = APIRouter(tags=["Email Attachments"])


@attachment_router.get(
    "/emails/{id}/attachments/{att_id}",
    dependencies=[Depends(require_permission("mail:read"))],
)
async def download_attachment_on_demand(
    id: uuid.UUID,
    att_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Fetches attachment on-demand from the mailserver and streams it securely to the client."""
    # 1. Fetch metadata record from DB
    meta_stmt = select(EmailAttachmentMetadata).where(
        EmailAttachmentMetadata.id == att_id,
        EmailAttachmentMetadata.incoming_email_id == id,
    )
    meta = (await db.execute(meta_stmt)).scalar_one_or_none()
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment metadata not found.")

    # 2. Fetch email record with mailbox
    email_stmt = (
        select(IncomingEmail)
        .options(selectinload(IncomingEmail.mailbox))
        .where(IncomingEmail.id == id)
    )
    email_rec = (await db.execute(email_stmt)).scalar_one_or_none()
    if not email_rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email record not found.")

    mailbox: Mailbox = email_rec.mailbox
    raw_password = decrypt_secret(mailbox.encrypted_password)

    imap_client = IMAPClient(
        host=mailbox.imap_host,
        port=mailbox.imap_port,
        username=mailbox.address,
        password=raw_password,
        tls_mode=mailbox.imap_tls_mode,
    )

    # 3. Fetch raw email bytes via IMAP BODY.PEEK
    raw_email_bytes = await imap_client.fetch_raw_email_peek(email_rec.imap_uid, email_rec.folder)
    if not raw_email_bytes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source email message is no longer available on the mailserver.",
        )

    # 4. Parse in-memory to find the matching attachment
    parsed = MimeEmailParser.parse_raw_message(raw_email_bytes)
    matched_part = None
    for part in parsed.attachments:
        if part.filename == meta.filename or (
            meta.content_id and part.content_id == meta.content_id
        ):
            matched_part = part
            break

    if not matched_part:
        # Fallback to first attachment if only one exists
        if len(parsed.attachments) == 1:
            matched_part = parsed.attachments[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attachment could not be extracted from source email.",
            )

    safe_filename = meta.filename.replace('"', '\\"')
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
        "Content-Security-Policy": "default-src 'none'",
        "X-Content-Type-Options": "nosniff",
    }

    return Response(
        content=matched_part.raw_bytes,
        media_type=meta.content_type,
        headers=headers,
    )
