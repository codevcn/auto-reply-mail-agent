"""Tests for Mail Ingestion Pipeline: Zero Raw Body Invariant R-04, Deduplication R-06, and On-Demand Fetch."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.core.crypto import encrypt_secret
from app.db.models.email import EmailJob, IncomingEmail, MailboxCheckpoint
from app.db.models.store import Mailbox, StoreProfile
from app.ingestion.service import MailFetchService, MailIngestionService
from app.mail.exceptions import SourceMessageUnavailableError
from app.mail.schemas import EmailHeaderMetadata
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def test_zero_raw_body_invariant_in_incoming_emails():
    """INVARIANT R-04: incoming_emails MUST NOT contain raw body or attachment binary columns."""
    columns = [c.name for c in IncomingEmail.__table__.columns]
    # Verify no raw body / attachments blob columns exist
    forbidden_columns = [
        "body",
        "raw_body",
        "body_raw",
        "body_html",
        "body_text",
        "attachments",
        "attachments_blob",
        "content_bytes",
    ]
    for col in forbidden_columns:
        assert col not in columns, f"Column '{col}' violates Invariant R-04 (No raw email replication in PostgreSQL)."


@pytest.mark.asyncio
async def test_ingest_mailbox_messages_creates_metadata_and_jobs(db_session: AsyncSession):
    """TC-INGEST-02: New email is ingested as metadata only, enqueues classify job, updates checkpoint."""
    # 1. Setup store, mailbox, checkpoint
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US",
        brand_name="Wrydeco",
        public_domain="wrydeco.com",
        canonical_domain="wrydeco.myshopify.com",
        status="active",
        activation_baseline_uid=100,
        uid_validity=8888,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password=encrypt_secret("test_pass"),
        imap_host="mail.wrydeco.com",
        imap_port=993,
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=8888,
        activation_baseline_uid=100,
        last_durably_enqueued_uid=100,
        state="active",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # 2. Mock IMAP discovery for UID 101
    mock_header = EmailHeaderMetadata(
        uid=101,
        message_id="<msg_101@client.com>",
        subject="Order Inquiry",
        from_address="customer@gmail.com",
        sender_name="Customer",
        to_addresses=["support@wrydeco.com"],
        date=datetime.datetime(2026, 10, 2, 12, 0, tzinfo=datetime.UTC),
        is_auto_submitted=False,
    )

    with (
        patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test,
        patch("app.mail.imap_client.IMAPClient.search_new_uids", new_callable=AsyncMock) as mock_search,
        patch("app.mail.imap_client.IMAPClient.fetch_header_metadata", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_test.return_value = MagicMock(success=True, uid_validity=8888, uid_next=102)
        mock_search.return_value = [101]
        mock_fetch.return_value = mock_header

        result = await MailIngestionService.ingest_mailbox_messages(
            db=db_session,
            mailbox_id=mailbox.id,
            folder="INBOX",
        )

        assert result.status == "success"
        assert result.new_emails_count == 1
        assert result.enqueued_jobs_count == 1
        assert result.highest_uid == 101

    # Verify incoming_emails record in DB
    email_stmt = select(IncomingEmail).where(IncomingEmail.mailbox_id == mailbox.id)
    saved_email = (await db_session.execute(email_stmt)).scalar_one_or_none()
    assert saved_email is not None
    assert saved_email.imap_uid == 101
    assert saved_email.sender_email == "customer@gmail.com"
    assert saved_email.subject == "Order Inquiry"
    assert saved_email.status == "pending"

    # Verify email_jobs queue item
    job_stmt = select(EmailJob).where(EmailJob.incoming_email_id == saved_email.id)
    saved_job = (await db_session.execute(job_stmt)).scalar_one_or_none()
    assert saved_job is not None
    assert saved_job.job_type == "classify"
    assert saved_job.status == "queued"


@pytest.mark.asyncio
async def test_ingestion_deduplication_uid_and_msgid(db_session: AsyncSession):
    """TC-INGEST-06: Duplicate email detection avoids double ingestion and double job queueing."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Preaureum US",
        brand_name="Preaureum",
        public_domain="preaureum.com",
        canonical_domain="preaureum.myshopify.com",
        status="active",
        activation_baseline_uid=50,
        uid_validity=7777,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@preaureum.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=7777,
        activation_baseline_uid=50,
        last_durably_enqueued_uid=50,
        state="active",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    mock_header = EmailHeaderMetadata(
        uid=51,
        message_id="<dup_msg@client.com>",
        subject="Duplicate check",
        from_address="customer@preaureum.com",
    )

    with (
        patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test,
        patch("app.mail.imap_client.IMAPClient.fetch_header_metadata", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_test.return_value = MagicMock(success=True, uid_validity=7777, uid_next=52)
        mock_fetch.return_value = mock_header

        # First ingestion
        res1 = await MailIngestionService.ingest_mailbox_messages(
            db=db_session, mailbox_id=mailbox.id, candidate_uids=[51]
        )
        assert res1.new_emails_count == 1

        # Second ingestion of the same UID
        res2 = await MailIngestionService.ingest_mailbox_messages(
            db=db_session, mailbox_id=mailbox.id, candidate_uids=[51]
        )
        assert res2.new_emails_count == 0
        assert res2.skipped_count == 1


@pytest.mark.asyncio
async def test_on_demand_email_fetch_missing_on_server(db_session: AsyncSession):
    """Invariant R-04: On-demand fetch marks email as manual_review if message is deleted from mailserver."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Chillgen US",
        brand_name="Chillgen",
        public_domain="chillgen.com",
        canonical_domain="chillgen.myshopify.com",
        status="active",
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@chillgen.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    email_item = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=999,
        uidvalidity=1234,
        sender_email="buyer@chillgen.com",
        recipient_email="support@chillgen.com",
        subject="Missing on server",
        received_at=datetime.datetime.now(datetime.UTC),
        status="pending",
    )
    db_session.add(email_item)
    await db_session.commit()

    with patch("app.mail.imap_client.IMAPClient.fetch_raw_email_peek", new_callable=AsyncMock) as mock_raw:
        # Server returns None (message deleted or missing)
        mock_raw.return_value = None

        with pytest.raises(SourceMessageUnavailableError):
            await MailFetchService.fetch_email_content(db_session, email_item.id)

    # Check that status was updated to manual_review
    await db_session.refresh(email_item)
    assert email_item.status == "manual_review"
    assert email_item.manual_review_reason == "SOURCE_MESSAGE_UNAVAILABLE"


class MagicMock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
