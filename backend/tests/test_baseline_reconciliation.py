"""Tests for Baseline UID discovery, Reconciliation Poller, and UIDVALIDITY safeguards."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.core.crypto import encrypt_secret
from app.db.models.email import MailboxCheckpoint
from app.db.models.store import Mailbox, StoreProfile
from app.ingestion.reconciliation import ReconciliationPoller
from app.ingestion.service import MailIngestionService
from app.mail.schemas import EmailHeaderMetadata
from app.store.services import StoreWizardService
from sqlalchemy.ext.asyncio import AsyncSession


class SimpleMock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_activation_baseline_uid_ignores_historical_emails(db_session: AsyncSession):
    """TC-INGEST-01: Baseline UID ignores pre-existing emails upon store activation (R-04)."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco Baseline Test",
        brand_name="Wrydeco",
        public_domain="wrydeco.com",
        canonical_domain="wrydeco.myshopify.com",
        status="active",
        activation_baseline_uid=100,
        uid_validity=5555,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=5555,
        activation_baseline_uid=100,
        last_durably_enqueued_uid=100,
        state="active",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # Server contains UIDs 1 to 100 (historical) and none above 100
    with (
        patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test,
        patch("app.mail.imap_client.IMAPClient.search_new_uids", new_callable=AsyncMock) as mock_search,
    ):
        mock_test.return_value = SimpleMock(success=True, uid_validity=5555, uid_next=101)
        mock_search.return_value = []  # No UID > 100

        result = await MailIngestionService.ingest_mailbox_messages(
            db=db_session,
            mailbox_id=mailbox.id,
            folder="INBOX",
        )

        assert result.status == "success"
        assert result.new_emails_count == 0
        assert result.skipped_count == 0


@pytest.mark.asyncio
async def test_reconciliation_poller_catches_missed_emails(db_session: AsyncSession):
    """TC-INGEST-03: 5-minute reconciliation catches multiple missed emails during disconnect."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Reconcile Store",
        brand_name="Reconcile",
        public_domain="reconcile.com",
        canonical_domain="reconcile.myshopify.com",
        status="active",
        activation_baseline_uid=100,
        uid_validity=6666,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@reconcile.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=6666,
        activation_baseline_uid=100,
        last_durably_enqueued_uid=100,
        state="active",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # 3 emails (102, 103, 104) missed during listener downtime
    missed_uids = [102, 103, 104]

    def mock_fetch_meta(uid, folder):
        return EmailHeaderMetadata(
            uid=uid,
            message_id=f"<msg_{uid}@client.com>",
            subject=f"Inquiry {uid}",
            from_address=f"user_{uid}@example.com",
            date=datetime.datetime.now(datetime.UTC),
        )

    with (
        patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test,
        patch("app.mail.imap_client.IMAPClient.search_new_uids", new_callable=AsyncMock) as mock_search,
        patch("app.mail.imap_client.IMAPClient.fetch_header_metadata", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_test.return_value = SimpleMock(success=True, uid_validity=6666, uid_next=105)
        mock_search.return_value = missed_uids
        mock_fetch.side_effect = mock_fetch_meta

        poller = ReconciliationPoller(interval_seconds=300)
        results = await poller.run_once(db_session)

        assert len(results) == 1
        assert results[0].new_emails_count == 3
        assert results[0].highest_uid == 104

        # Checkpoint is durably updated
        await db_session.refresh(checkpoint)
        assert checkpoint.last_durably_enqueued_uid == 104
        assert checkpoint.last_reconciled_at is not None


@pytest.mark.asyncio
async def test_uidvalidity_changed_safeguard_pauses_ingestion(db_session: AsyncSession):
    """TC-INGEST-05: UIDVALIDITY mismatch triggers safety protocol and blocks ingestion."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="UIDVALIDITY Safeguard Store",
        brand_name="Safeguard",
        public_domain="safeguard.com",
        canonical_domain="safeguard.myshopify.com",
        status="active",
        activation_baseline_uid=100,
        uid_validity=8888,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@safeguard.com",
        encrypted_password=encrypt_secret("test_pass"),
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

    # Server reports new/different UIDVALIDITY = 9999
    with patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test:
        mock_test.return_value = SimpleMock(success=True, uid_validity=9999, uid_next=10)

        result = await MailIngestionService.ingest_mailbox_messages(
            db=db_session,
            mailbox_id=mailbox.id,
            folder="INBOX",
        )

        assert result.status == "uidvalidity_changed"
        await db_session.refresh(checkpoint)
        assert checkpoint.state == "uidvalidity_changed"
        assert checkpoint.last_error_code == "IMAP_UIDVALIDITY_CHANGED"


@pytest.mark.asyncio
async def test_store_reactivation_preserves_existing_checkpoint(db_session: AsyncSession):
    """Section 2.2: Re-enabling a store profile preserves existing checkpoint; does not reset baseline."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Reactivation Store",
        brand_name="Reactivation",
        public_domain="reactivate.com",
        canonical_domain="reactivate.myshopify.com",
        status="paused",
        activation_baseline_uid=250,
        uid_validity=4444,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@reactivate.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=4444,
        activation_baseline_uid=250,
        last_durably_enqueued_uid=280,  # Progressed before pause
        state="paused",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # Re-activate profile
    success, msg = await StoreWizardService.activate_profile(
        db=db_session,
        store_id=store.id,
        mailbox_tested=True,
        proxy_tested=True,
        shopify_tested=True,
    )

    assert success is True
    await db_session.refresh(store)
    await db_session.refresh(checkpoint)

    # Baseline UID must NOT be reset back to 0 or 100
    assert store.activation_baseline_uid == 250
    assert checkpoint.activation_baseline_uid == 250
    assert checkpoint.last_durably_enqueued_uid == 280
    assert checkpoint.state == "active"
