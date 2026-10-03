"""Tests for Spam Isolation (R-08), Manual Review Routing (R-21), and Classification Override.

Requirements: Invariants R-08, R-21, R-23; Sections 13 & 18.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from app.db.models.audit import AuditEvent
from app.db.models.email import EmailClassification, EmailJob, IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.queue.schemas import OverrideClassificationRequest
from app.queue.service import TransactionalQueueService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def setup_test_store(db_session: AsyncSession) -> tuple[StoreProfile, Mailbox]:
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US",
        brand_name="Wrydeco",
        public_domain="wrydeco.com",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        imap_host="mail.wrydeco.com",
        imap_port=993,
        imap_tls_mode="SSL_TLS",
        smtp_host="mail.wrydeco.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        encrypted_password="enc_password",
    )
    db_session.add(store)
    db_session.add(mailbox)
    await db_session.commit()
    return store, mailbox


@pytest.mark.asyncio
async def test_spam_hidden_from_regular_queues(
    db_session: AsyncSession, setup_test_store: tuple[StoreProfile, Mailbox]
):
    """INVARIANT R-08: Spam emails are hidden from regular reply queues and visible only in Spam tab."""
    store, mailbox = setup_test_store

    # 1. Normal inquiry email
    normal_email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=101,
        uidvalidity=1,
        sender_email="buyer@gmail.com",
        recipient_email="support@wrydeco.com",
        subject="Product question",
        received_at=datetime.datetime.now(datetime.UTC),
        status="pending_approval",
        classification_category="product_inquiry",
        spam_status="not_spam",
    )

    # 2. Spam email
    spam_email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=102,
        uidvalidity=1,
        sender_email="spammer@lottery.xyz",
        recipient_email="support@wrydeco.com",
        subject="CLAIM YOUR LOTTERY PRIZE",
        received_at=datetime.datetime.now(datetime.UTC),
        status="spam",
        classification_category="other",
        spam_status="spam",
    )

    db_session.add(normal_email)
    db_session.add(spam_email)
    await db_session.commit()

    # Verify regular queue queries DO NOT contain spam
    ready_items = await TransactionalQueueService.get_queue_emails(
        db_session, "ready-to-review", store_id=store.id
    )
    assert any(item.id == normal_email.id for item in ready_items.items)
    assert not any(item.id == spam_email.id for item in ready_items.items)

    product_items = await TransactionalQueueService.get_queue_emails(
        db_session, "product-inquiry", store_id=store.id
    )
    assert any(item.id == normal_email.id for item in product_items.items)
    assert not any(item.id == spam_email.id for item in product_items.items)

    # Verify Spam queue contains only spam
    spam_items = await TransactionalQueueService.get_queue_emails(
        db_session, "spam", store_id=store.id
    )
    assert any(item.id == spam_email.id for item in spam_items.items)
    assert not any(item.id == normal_email.id for item in spam_items.items)

    # Verify stats
    stats = await TransactionalQueueService.get_queue_stats(db_session, store_id=store.id)
    assert stats.spam >= 1
    assert stats.ready_to_review >= 1


@pytest.mark.asyncio
async def test_unmark_spam_recovers_email_and_audits(
    db_session: AsyncSession,
    setup_test_store: tuple[StoreProfile, Mailbox],
    bootstrap_user,
):
    """INVARIANT R-08: Unmarking spam restores email to active workflow and records audit log."""
    store, mailbox = setup_test_store

    spam_email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=105,
        uidvalidity=1,
        sender_email="customer_confused@gmail.com",
        recipient_email="support@wrydeco.com",
        subject="Need invoice please",
        received_at=datetime.datetime.now(datetime.UTC),
        status="spam",
        classification_category="product_inquiry",
        spam_status="spam",
    )
    db_session.add(spam_email)
    await db_session.commit()

    operator = await bootstrap_user("op_unmark")
    recovered = await TransactionalQueueService.unmark_spam(
        db_session, spam_email.id, user_id=operator.id
    )

    assert recovered.spam_status == "not_spam"
    assert recovered.status in ("classified", "pending", "pending_approval")

    # Verify user classification recorded
    class_stmt = select(EmailClassification).where(
        EmailClassification.incoming_email_id == spam_email.id,
        EmailClassification.source == "user",
    )
    class_rec = (await db_session.execute(class_stmt)).scalar_one_or_none()
    assert class_rec is not None
    assert class_rec.spam_status == "not_spam"
    assert "USER_UNMARK_SPAM" in (class_rec.reason_codes or [])

    # Verify audit event
    audit_stmt = select(AuditEvent).where(
        AuditEvent.target_id == str(spam_email.id),
        AuditEvent.event_type == "EMAIL_UNMARK_SPAM",
    )
    audit_rec = (await db_session.execute(audit_stmt)).scalar_one_or_none()
    assert audit_rec is not None


@pytest.mark.asyncio
async def test_override_classification_updates_and_enqueues(
    db_session: AsyncSession,
    setup_test_store: tuple[StoreProfile, Mailbox],
    bootstrap_user,
):
    """Operator overrides email classification to product inquiry and enqueues draft."""
    store, mailbox = setup_test_store

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=108,
        uidvalidity=1,
        sender_email="buyer2@gmail.com",
        recipient_email="support@wrydeco.com",
        subject="Question",
        received_at=datetime.datetime.now(datetime.UTC),
        status="manual_review",
        classification_category="other",
        manual_review_reason="UNCERTAIN_INTENT",
    )
    db_session.add(email_rec)
    await db_session.commit()

    operator = await bootstrap_user("op_override")
    override_req = OverrideClassificationRequest(
        intent="product_inquiry",
        customer_status="has_order_record",
        spam_status="not_spam",
        generate_draft=True,
        notes="Customer asked about art frame width.",
    )

    updated = await TransactionalQueueService.override_classification(
        db_session, email_rec.id, user_id=operator.id, override_data=override_req
    )

    assert updated.classification_category == "product_inquiry"
    assert updated.customer_status == "has_order_record"
    assert updated.status == "classified"
    assert updated.manual_review_reason is None

    # Check job was enqueued
    job_stmt = select(EmailJob).where(
        EmailJob.incoming_email_id == email_rec.id,
        EmailJob.job_type == "generate_draft",
    )
    job = (await db_session.execute(job_stmt)).scalar_one_or_none()
    assert job is not None
    assert job.status == "queued"
