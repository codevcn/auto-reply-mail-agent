"""Empirical Adversarial Stress Test Suite for Phase 3 Mail Ingestion.

Covers:
1. Invariant R-06: Strict \\Seen flag preservation and EXAMINE (Read-Only) IMAP contract.
2. Invariant R-04: Activation Baseline UID boundary tests (UID <= baseline ignored, UID > baseline ingested, non-contiguous UIDs).
3. Invariant R-04: Store Re-activation baseline immutability (checkpoint never rolls back or resets).
4. Failsafe UIDVALIDITY_CHANGED: Protocol halt on server UIDVALIDITY change, no silent data corruption.
5. Invariant R-03: Piezaprint multi-layer absolute exclusion (Pydantic, Client, Service, API endpoints, casing/subdomain attacks).
6. Invariant R-04: Physical absence of raw email body and attachment blobs in PostgreSQL schema.
7. MailFetchService: On-demand raw fetch, HTML sanitization, and SOURCE_MESSAGE_UNAVAILABLE failsafe.
8. Transactional Queue: Concurrency locking, exponential retry lifecycle, and dead-letter manual review escalation.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.crypto import encrypt_secret
from app.db.models.email import EmailJob, IncomingEmail, MailboxCheckpoint
from app.db.models.store import Mailbox, StoreProfile
from app.db.session import get_db
from app.ingestion.reconciliation import ReconciliationPoller
from app.ingestion.service import MailFetchService, MailIngestionService
from app.mail.exceptions import PiezaprintExclusionError, SourceMessageUnavailableError
from app.mail.imap_client import IMAPClient
from app.mail.schemas import EmailHeaderMetadata, MailboxCandidateTestRequest
from app.mail.smtp_client import SMTPClient
from app.main import app
from app.queue.service import TransactionalQueueService
from app.store.services import StoreWizardService
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


# ==============================================================================
# Helper Mock Class
# ==============================================================================
class MockStatusResult:
    def __init__(self, success: bool = True, uid_validity: int = 5555, uid_next: int = 101, error: str | None = None):
        self.success = success
        self.uid_validity = uid_validity
        self.uid_next = uid_next
        self.error = error
        self.message_count = 10
        self.unseen_count = 3
        self.capabilities = ["IMAP4rev1", "IDLE"]


# ==============================================================================
# 1. Invariant R-06: \\Seen Preservation & Read-Only Contract
# ==============================================================================
def test_invariant_r06_seen_preservation_and_examine_contract():
    """R-06 Adversarial: IMAP client must strictly use EXAMINE and BODY.PEEK, NEVER mutating \\Seen or using SELECT."""
    client = IMAPClient(
        host="mail.wrydeco.com",
        port=993,
        username="support@wrydeco.com",
        password="secret_password",
    )

    mock_imap = MagicMock()
    mock_imap.examine.return_value = ("OK", [b"25"])
    mock_imap.select.side_effect = RuntimeError("ADVERSARIAL_VIOLATION: select() must NEVER be called!")

    header_raw = (
        b"From: Customer <buyer@example.com>\r\n"
        b"To: support@wrydeco.com\r\n"
        b"Subject: Product Details\r\n"
        b"Message-ID: <msg_500@client.com>\r\n"
        b"Date: Fri, 02 Oct 2026 14:00:00 +0000\r\n\r\n"
    )
    # Server returns message with flags (e.g. \\Draft, but UNSEEN)
    mock_imap.uid.return_value = ("OK", [(b"500 (FLAGS (\\Draft))", header_raw)])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        # 1. Test header metadata fetch
        meta = client._sync_fetch_header_metadata(uid=500, folder="INBOX")

        # Must call examine, never select
        mock_imap.examine.assert_called_with("INBOX")
        mock_imap.select.assert_not_called()

        # Must use BODY.PEEK
        uid_calls = mock_imap.uid.call_args_list
        fetch_call = [c for c in uid_calls if c[0][0] == "FETCH"][0]
        fetch_cmd_str = fetch_call[0][2]
        assert "BODY.PEEK" in fetch_cmd_str, "Command must contain BODY.PEEK"
        assert "BODY[" not in fetch_cmd_str.replace("BODY.PEEK", ""), "BODY[] without PEEK mutates \\Seen!"

        # Must NEVER call store to set \\Seen
        assert not mock_imap.store.called, "STORE command must never be issued!"

        assert meta is not None
        assert meta.uid == 500
        assert meta.from_address == "buyer@example.com"
        assert meta.flags == ["\\Draft"]

        # 2. Test raw RFC822 email peek fetch
        mock_imap.reset_mock()
        mock_imap.examine.return_value = ("OK", [b"25"])
        mock_imap.uid.return_value = ("OK", [(b"500 (BODY[PEEK] {120})", b"Raw email body content")])

        raw_bytes = client._sync_fetch_raw_rfc822_peek(uid=500, folder="INBOX")
        mock_imap.examine.assert_called_with("INBOX")
        mock_imap.select.assert_not_called()
        assert not mock_imap.store.called

        raw_fetch_call = mock_imap.uid.call_args_list[0][0]
        assert raw_fetch_call[0] == "FETCH"
        assert "(BODY.PEEK[])" in raw_fetch_call[2]
        assert raw_bytes == b"Raw email body content"


# ==============================================================================
# 2. Invariant R-04: Activation Baseline UID Boundary Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_invariant_r04_baseline_uid_strict_boundary_adversarial(db_session: AsyncSession):
    """R-04 Adversarial: Strictly enforce UID > baseline_uid. UIDs <= baseline must be silently ignored."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Baseline Stress Store",
        brand_name="BaselineBrand",
        public_domain="baseline.com",
        canonical_domain="baseline.myshopify.com",
        status="active",
        activation_baseline_uid=100,
        uid_validity=7777,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@baseline.com",
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
        activation_baseline_uid=100,
        last_durably_enqueued_uid=100,
        state="active",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # Candidate UIDs containing edge cases:
    # 50: strictly historical
    # 99: baseline - 1
    # 100: exact baseline (MUST NOT be ingested)
    # 101: baseline + 1 (MUST be ingested)
    # 105: new email (MUST be ingested)
    # 120: new email (MUST be ingested)
    candidate_uids = [50, 99, 100, 101, 105, 120]

    def mock_meta(uid, folder):
        return EmailHeaderMetadata(
            uid=uid,
            message_id=f"<msg_{uid}@baseline.com>",
            subject=f"Subject {uid}",
            from_address=f"user_{uid}@example.com",
            date=datetime.datetime.now(datetime.UTC),
        )

    with (
        patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test,
        patch("app.mail.imap_client.IMAPClient.fetch_header_metadata", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_test.return_value = MockStatusResult(success=True, uid_validity=7777, uid_next=121)
        mock_fetch.side_effect = mock_meta

        result = await MailIngestionService.ingest_mailbox_messages(
            db=db_session,
            mailbox_id=mailbox.id,
            folder="INBOX",
            candidate_uids=candidate_uids,
        )

        assert result.status == "success"
        # Only 101, 105, 120 should be ingested! UIDs 50, 99, 100 are ignored
        assert result.new_emails_count == 3
        assert result.enqueued_jobs_count == 3
        assert result.highest_uid == 120

        # Verify DB records
        stmt = select(IncomingEmail).where(IncomingEmail.mailbox_id == mailbox.id)
        saved_emails = (await db_session.execute(stmt)).scalars().all()
        saved_uids = {e.imap_uid for e in saved_emails}
        assert saved_uids == {101, 105, 120}
        assert 100 not in saved_uids
        assert 99 not in saved_uids
        assert 50 not in saved_uids

        # Verify checkpoint state
        await db_session.refresh(checkpoint)
        assert checkpoint.last_durably_enqueued_uid == 120


# ==============================================================================
# 3. Invariant R-04: Re-activation Preserves Baseline & Last Enqueued UID
# ==============================================================================
@pytest.mark.asyncio
async def test_invariant_r04_reactivation_baseline_immutability(db_session: AsyncSession):
    """Section 2.2: Pausing and reactivating a store profile preserves baseline and last enqueued UID."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Pausable Store",
        brand_name="Pausable",
        public_domain="pausable.com",
        canonical_domain="pausable.myshopify.com",
        status="paused",
        activation_baseline_uid=500,
        uid_validity=3333,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@pausable.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="paused",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=3333,
        activation_baseline_uid=500,
        last_durably_enqueued_uid=550,  # Processed up to 550 before pause
        state="paused",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # Re-activate store
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

    # Immutability verification
    assert store.status == "active"
    assert store.activation_baseline_uid == 500
    assert checkpoint.state == "active"
    assert checkpoint.activation_baseline_uid == 500
    assert checkpoint.last_durably_enqueued_uid == 550


# ==============================================================================
# 4. Failsafe UIDVALIDITY_CHANGED Safeguard
# ==============================================================================
@pytest.mark.asyncio
async def test_failsafe_uidvalidity_changed_halts_ingestion_safely(db_session: AsyncSession):
    """Section 11.3: Mailserver changing UIDVALIDITY immediately halts ingestion and sets safe state."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="UIDValidity Store",
        brand_name="UIDValidityBrand",
        public_domain="uidvalidity.com",
        canonical_domain="uidvalidity.myshopify.com",
        status="active",
        activation_baseline_uid=100,
        uid_validity=1111,
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@uidvalidity.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    checkpoint = MailboxCheckpoint(
        id=uuid.uuid4(),
        mailbox_id=mailbox.id,
        folder="INBOX",
        uid_validity=1111,
        activation_baseline_uid=100,
        last_durably_enqueued_uid=100,
        state="active",
    )
    db_session.add(checkpoint)
    await db_session.commit()

    # Server unexpectedly returns UIDVALIDITY 9999
    with patch("app.mail.imap_client.IMAPClient.test_connection", new_callable=AsyncMock) as mock_test:
        mock_test.return_value = MockStatusResult(success=True, uid_validity=9999, uid_next=20)

        result = await MailIngestionService.ingest_mailbox_messages(
            db=db_session,
            mailbox_id=mailbox.id,
            folder="INBOX",
        )

        assert result.status == "uidvalidity_changed"
        assert result.new_emails_count == 0

        await db_session.refresh(checkpoint)
        assert checkpoint.state == "uidvalidity_changed"
        assert checkpoint.last_error_code == "IMAP_UIDVALIDITY_CHANGED"

    # Verify ReconciliationPoller skips checkpoints in uidvalidity_changed state
    poller = ReconciliationPoller(interval_seconds=300)
    poller_results = await poller.run_once(db_session)
    assert len(poller_results) == 0, "Poller must not process mailboxes in uidvalidity_changed state!"


# ==============================================================================
# 5. Invariant R-03: Piezaprint Multi-Layer Exclusion
# ==============================================================================
@pytest.mark.asyncio
async def test_invariant_r03_piezaprint_exclusion_multi_layer_adversarial(db_session: AsyncSession):
    """R-03 Adversarial: Piezaprint exclusion tested against case alterations, subdomains, and API vectors."""
    forbidden_addresses = [
        "support@piezaprint.com",
        "SUPPORT@PIEZAPRINT.COM",
        "Admin@PieZaPrint.Com",
        "orders@us.piezaprint.com",
        "bot@piezaprint-service.com",
    ]

    # Layer 1: Pydantic Candidate Schema validation
    for addr in forbidden_addresses:
        with pytest.raises(ValidationError):
            MailboxCandidateTestRequest(address=addr, password="dummy_password")

    # Layer 2: Client Instantiation (IMAP and SMTP)
    for addr in forbidden_addresses:
        with pytest.raises(PiezaprintExclusionError):
            IMAPClient(username=addr, password="pwd")
        with pytest.raises(PiezaprintExclusionError):
            SMTPClient(username=addr, password="pwd")

    # Layer 3: Host exclusion
    with pytest.raises(PiezaprintExclusionError):
        IMAPClient(host="mail.piezaprint.com", username="support@other.com", password="pwd")
    with pytest.raises(PiezaprintExclusionError):
        SMTPClient(host="smtp.piezaprint.com", username="support@other.com", password="pwd")

    # Layer 4: Service Ingestion Level
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Pieza Malicious Store",
        brand_name="Pieza",
        public_domain="piezaprint.com",
        canonical_domain="piezaprint.myshopify.com",
        status="active",
    )
    db_session.add(store)
    await db_session.flush()

    mb_malicious = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@piezaprint.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mb_malicious)
    await db_session.commit()

    with pytest.raises(PiezaprintExclusionError):
        await MailIngestionService.ingest_mailbox_messages(
            db=db_session, mailbox_id=mb_malicious.id, folder="INBOX"
        )

    # Layer 5: API Router Level
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        from app.auth.dependencies import get_current_user
        from app.db.models.user import User

        mock_user = User(
            id=uuid.uuid4(),
            username="admin",
            normalized_username="admin",
            status="active",
        )
        app.dependency_overrides[get_current_user] = lambda: mock_user

        resp = await ac.post(
            "/api/mail/test-connection",
            json={"address": "support@piezaprint.com", "password": "valid_pwd_123"},
        )
        assert resp.status_code == 422 or resp.status_code == 400

        # Test store mailbox endpoint
        resp_store = await ac.post(f"/api/stores/{store.id}/test-mailbox")
        assert resp_store.status_code == 400
        data = resp_store.json()
        assert data["detail"]["error_code"] == "STORE_EXCLUDED_FROM_SYSTEM"

    app.dependency_overrides.clear()


# ==============================================================================
# 6. Invariant R-04: Zero Raw Body in PostgreSQL Schema
# ==============================================================================
def test_invariant_r04_zero_raw_body_in_postgresql_schema():
    """R-04 Physical Schema Audit: Zero raw body, HTML, or attachment blob columns in incoming_emails."""
    incoming_cols = {c.name.lower() for c in IncomingEmail.__table__.columns}
    forbidden_terms = ["body", "raw_body", "html", "attachments_blob", "raw_mime", "payload"]

    for col in incoming_cols:
        for term in forbidden_terms:
            assert term not in col, f"Schema violation: Column '{col}' contains forbidden term '{term}'!"

    # Ensure metadata columns exist
    assert "sender_email" in incoming_cols
    assert "subject" in incoming_cols
    assert "received_at" in incoming_cols
    assert "imap_uid" in incoming_cols
    assert "uidvalidity" in incoming_cols
    assert "status" in incoming_cols


# ==============================================================================
# 7. MailFetchService: On-Demand Fetch & SOURCE_MESSAGE_UNAVAILABLE
# ==============================================================================
@pytest.mark.asyncio
async def test_mail_fetch_service_on_demand_and_failsafe(db_session: AsyncSession):
    """Section 11.7: On-demand raw MIME fetch, HTML sanitization, and SOURCE_MESSAGE_UNAVAILABLE handling."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Fetch Store",
        brand_name="FetchBrand",
        public_domain="fetch.com",
        canonical_domain="fetch.myshopify.com",
        status="active",
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@fetch.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    email_record = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=777,
        uidvalidity=9999,
        sender_email="customer@example.com",
        recipient_email="support@fetch.com",
        subject="Dirty HTML Test",
        received_at=datetime.datetime.now(datetime.UTC),
        status="pending",
    )
    db_session.add(email_record)
    await db_session.commit()

    # 1. Successful on-demand fetch with dangerous HTML payload
    raw_dirty_email = (
        b"From: customer@example.com\r\n"
        b"To: support@fetch.com\r\n"
        b"Subject: Dirty HTML Test\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body>"
        b"<p>Hello Support!</p>"
        b"<script>alert('xss attack');</script>"
        b"<iframe src='http://evil.com'></iframe>"
        b"<img src='cat.jpg' onload='stealCookies()' />"
        b"</body></html>"
    )

    with patch("app.mail.imap_client.IMAPClient.fetch_raw_email_peek", new_callable=AsyncMock) as mock_raw:
        mock_raw.return_value = raw_dirty_email

        content_resp = await MailFetchService.fetch_email_content(db_session, email_record.id)
        assert content_resp.email_id == email_record.id
        # Assert dangerous scripts and iframes were sanitized away
        assert "<script>" not in content_resp.body_html_sanitized
        assert "<iframe>" not in content_resp.body_html_sanitized
        assert "onload" not in content_resp.body_html_sanitized
        assert "Hello Support!" in content_resp.body_html_sanitized

    # 2. Email deleted on mailserver (SOURCE_MESSAGE_UNAVAILABLE)
    with patch("app.mail.imap_client.IMAPClient.fetch_raw_email_peek", new_callable=AsyncMock) as mock_raw:
        mock_raw.return_value = None  # Missing on server

        with pytest.raises(SourceMessageUnavailableError):
            await MailFetchService.fetch_email_content(db_session, email_record.id)

        await db_session.refresh(email_record)
        assert email_record.status == "manual_review"
        assert email_record.manual_review_reason == "SOURCE_MESSAGE_UNAVAILABLE"


# ==============================================================================
# 8. Transactional Queue Lifecycle & Job Concurrency
# ==============================================================================
@pytest.mark.asyncio
async def test_transactional_queue_lifecycle_and_dead_letter(db_session: AsyncSession):
    """Section 18 & Invariant R-35: Queue job acquire, failure retry backoff, and dead-letter manual review."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Queue Store",
        brand_name="QueueBrand",
        public_domain="queue.com",
        canonical_domain="queue.myshopify.com",
        status="active",
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@queue.com",
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
        imap_uid=888,
        uidvalidity=5555,
        sender_email="queue_user@example.com",
        recipient_email="support@queue.com",
        subject="Queue Test",
        received_at=datetime.datetime.now(datetime.UTC),
        status="pending",
    )
    db_session.add(email_item)
    await db_session.flush()

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_item.id,
        store_profile_id=store.id,
        job_type="classify",
        status="queued",
        attempts=0,
        max_attempts=2,
    )
    db_session.add(job)
    await db_session.commit()

    # Step 1: Worker 1 acquires job
    acquired = await TransactionalQueueService.acquire_next_job(
        session=db_session, worker_id="worker_alpha", job_type="classify"
    )
    assert acquired is not None
    assert acquired.id == job.id
    assert acquired.status == "processing"
    assert acquired.locked_by == "worker_alpha"
    assert acquired.attempts == 1

    # Step 2: Job fails first attempt (attempts < max_attempts -> requeued)
    res_fail1 = await TransactionalQueueService.fail_job(
        session=db_session, job_id=job.id, error_message="Network glitch"
    )
    assert res_fail1 is True
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.locked_by is None

    # Step 3: Re-acquire for attempt 2
    # Adjust scheduled_at back so it's immediately available
    job.scheduled_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5)
    await db_session.commit()

    acquired2 = await TransactionalQueueService.acquire_next_job(
        session=db_session, worker_id="worker_beta", job_type="classify"
    )
    assert acquired2 is not None
    assert acquired2.attempts == 2

    # Step 4: Job fails second attempt (attempts >= max_attempts -> failed & manual_review)
    res_fail2 = await TransactionalQueueService.fail_job(
        session=db_session, job_id=job.id, error_message="Permanent failure"
    )
    assert res_fail2 is True
    await db_session.refresh(job)
    assert job.status == "failed"

    await db_session.refresh(email_item)
    assert email_item.status == "manual_review"
    assert email_item.manual_review_reason == "JOB_MAX_ATTEMPTS_EXCEEDED"
