"""MailService: Business coordination layer for Mailbox testing and Store integration."""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt_secret
from app.db.models.store import Mailbox, StoreProfile
from app.mail.exceptions import PiezaprintExclusionError
from app.mail.imap_client import IMAPClient
from app.mail.schemas import (
    MailboxCandidateTestRequest,
    MailboxConnectionTestResponse,
)
from app.mail.smtp_client import SMTPClient

EXCLUDED_DOMAINS = ["piezaprint.com", "piezaprint"]


class MailService:
    """Coordinates IMAP/SMTP testing, credential decryption, and mailbox record status updates."""

    @staticmethod
    def check_piezaprint_exclusion(address: str) -> None:
        """Enforces invariant R-03: Piezaprint mailboxes are strictly excluded."""
        cleaned = address.strip().lower()
        if any(exc in cleaned for exc in EXCLUDED_DOMAINS):
            raise PiezaprintExclusionError()

    @staticmethod
    async def test_candidate_mailbox(
        data: MailboxCandidateTestRequest,
    ) -> MailboxConnectionTestResponse:
        """Tests an uncommitted candidate mailbox credentials payload."""
        start_time = time.perf_counter()
        MailService.check_piezaprint_exclusion(data.address)

        imap_client = IMAPClient(
            host=data.imap_host,
            port=data.imap_port,
            username=data.address,
            password=data.password,
            tls_mode=data.imap_tls_mode,
        )

        smtp_client = SMTPClient(
            host=data.smtp_host,
            port=data.smtp_port,
            username=data.address,
            password=data.password,
            tls_mode=data.smtp_tls_mode,
        )

        # Execute tests concurrently
        imap_res, smtp_res = await asyncio.gather(
            imap_client.test_connection(),
            smtp_client.test_connection(),
        )

        overall_latency = int((time.perf_counter() - start_time) * 1000)
        overall_success = imap_res.success and smtp_res.success

        error_code = None
        if not imap_res.success:
            error_code = imap_res.error
        elif not smtp_res.success:
            error_code = smtp_res.error

        return MailboxConnectionTestResponse(
            success=overall_success,
            address=data.address,
            tested_at=datetime.datetime.now(datetime.UTC),
            overall_latency_ms=overall_latency,
            imap=imap_res,
            smtp=smtp_res,
            error_code=error_code,
            detail=imap_res.detail or smtp_res.detail,
        )

    @staticmethod
    async def test_store_mailbox(
        db: AsyncSession,
        store_id: uuid.UUID,
    ) -> MailboxConnectionTestResponse:
        """Tests the configured mailbox of an existing Store Profile and persists health state."""
        start_time = time.perf_counter()

        stmt = (
            select(StoreProfile)
            .options(selectinload(StoreProfile.mailboxes))
            .where(StoreProfile.id == store_id)
        )
        res = await db.execute(stmt)
        store = res.scalar_one_or_none()
        if not store:
            raise ValueError("STORE_NOT_FOUND")

        if not store.mailboxes:
            raise ValueError("MAILBOX_NOT_CONFIGURED")

        mailbox: Mailbox = store.mailboxes[0]
        MailService.check_piezaprint_exclusion(mailbox.address)

        # Decrypt password via Envelope Encryption
        raw_password = decrypt_secret(mailbox.encrypted_password)

        imap_client = IMAPClient(
            host=mailbox.imap_host,
            port=mailbox.imap_port,
            username=mailbox.address,
            password=raw_password,
            tls_mode=mailbox.imap_tls_mode,
        )

        smtp_client = SMTPClient(
            host=mailbox.smtp_host,
            port=mailbox.smtp_port,
            username=mailbox.address,
            password=raw_password,
            tls_mode=mailbox.smtp_tls_mode,
        )

        imap_res, smtp_res = await asyncio.gather(
            imap_client.test_connection(),
            smtp_client.test_connection(),
        )

        now_utc = datetime.datetime.now(datetime.UTC)
        if imap_res.success:
            mailbox.last_imap_success_at = now_utc
        if smtp_res.success:
            mailbox.last_smtp_auth_success_at = now_utc

        if imap_res.success and smtp_res.success:
            mailbox.status = "active"
            mailbox.last_error_code = None
        else:
            mailbox.status = "error"
            mailbox.last_error_code = imap_res.error or smtp_res.error

        mailbox.row_version += 1
        await db.commit()

        overall_latency = int((time.perf_counter() - start_time) * 1000)
        overall_success = imap_res.success and smtp_res.success

        return MailboxConnectionTestResponse(
            success=overall_success,
            address=mailbox.address,
            tested_at=now_utc,
            overall_latency_ms=overall_latency,
            imap=imap_res,
            smtp=smtp_res,
            error_code=mailbox.last_error_code,
            detail=imap_res.detail or smtp_res.detail,
        )
