"""Tests for Transactional Queue: Row locking, lifecycle execution, and Queue stats/lists."""

from __future__ import annotations

import datetime
import uuid

import pytest
from app.core.crypto import encrypt_secret
from app.db.models.email import EmailJob, IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.queue.service import TransactionalQueueService
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_queue_acquire_and_row_locking(db_session: AsyncSession):
    """TC-QUEUE-01: Worker acquires next job, updates locked_at/locked_by, and increments attempts."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Queue Test Store",
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
        imap_uid=1,
        uidvalidity=100,
        sender_email="buyer@queue.com",
        recipient_email="support@queue.com",
        subject="Test queue",
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
        max_attempts=3,
        scheduled_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5),
    )
    db_session.add(job)
    await db_session.commit()

    # Worker 1 leases job
    acquired = await TransactionalQueueService.acquire_next_job(
        session=db_session, worker_id="worker_thread_1", job_type="classify"
    )

    assert acquired is not None
    assert acquired.id == job.id
    assert acquired.status == "processing"
    assert acquired.locked_by == "worker_thread_1"
    assert acquired.attempts == 1

    # Worker 2 attempts to lease -> None available
    acquired_2 = await TransactionalQueueService.acquire_next_job(
        session=db_session, worker_id="worker_thread_2", job_type="classify"
    )
    assert acquired_2 is None


@pytest.mark.asyncio
async def test_queue_fail_job_retry_and_dead_letter(db_session: AsyncSession):
    """TC-QUEUE-02: Failure below max_attempts schedules retry; exceeding max_attempts flags manual review."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Retry Test Store",
        brand_name="RetryBrand",
        public_domain="retry.com",
        canonical_domain="retry.myshopify.com",
        status="active",
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@retry.com",
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
        imap_uid=2,
        uidvalidity=100,
        sender_email="buyer@retry.com",
        recipient_email="support@retry.com",
        subject="Retry email",
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
        status="processing",
        attempts=1,
        max_attempts=2,
        scheduled_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(job)
    await db_session.commit()

    # 1. First failure -> attempts (1) < max_attempts (2) -> rescheduled
    retried = await TransactionalQueueService.fail_job(
        session=db_session, job_id=job.id, error_message="Temporary network hiccup"
    )
    assert retried is True
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.locked_at is None
    assert job.locked_by is None

    # 2. Worker leases again -> attempts becomes 2
    job.attempts = 2
    await db_session.commit()

    # Second failure -> attempts (2) >= max_attempts (2) -> failed & dead-letter
    failed = await TransactionalQueueService.fail_job(
        session=db_session, job_id=job.id, error_message="Permanent model failure"
    )
    assert failed is True
    await db_session.refresh(job)
    await db_session.refresh(email_item)
    assert job.status == "failed"
    assert email_item.status == "manual_review"
    assert email_item.manual_review_reason == "JOB_MAX_ATTEMPTS_EXCEEDED"


@pytest.mark.asyncio
async def test_queue_stats_and_filtering(db_session: AsyncSession):
    """TC-QUEUE-03: Statistics across all 7 queues and paginated email querying."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Stats Test Store",
        brand_name="StatsBrand",
        public_domain="stats.com",
        canonical_domain="stats.myshopify.com",
        status="active",
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@stats.com",
        encrypted_password=encrypt_secret("test_pass"),
        status="active",
    )
    db_session.add(mailbox)
    await db_session.flush()

    now = datetime.datetime.now(datetime.UTC)

    # Email 1: Waiting for approval (ready-to-review)
    e1 = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=10,
        uidvalidity=1,
        sender_email="e1@gmail.com",
        recipient_email=mailbox.address,
        subject="Subject 1",
        received_at=now,
        status="pending_approval",
    )
    # Email 2: Needs manual review
    e2 = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=11,
        uidvalidity=1,
        sender_email="e2@gmail.com",
        recipient_email=mailbox.address,
        subject="Subject 2",
        received_at=now,
        status="manual_review",
    )
    # Email 3: Product inquiry
    e3 = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=12,
        uidvalidity=1,
        sender_email="e3@gmail.com",
        recipient_email=mailbox.address,
        subject="Subject 3",
        received_at=now,
        status="pending",
        classification_category="product_inquiry",
    )

    db_session.add_all([e1, e2, e3])
    await db_session.commit()

    stats = await TransactionalQueueService.get_queue_stats(db_session, store_id=store.id)
    assert stats.ready_to_review == 1
    assert stats.needs_manual_review == 1
    assert stats.product_inquiry == 1
    assert stats.spam == 0

    # Query list for ready-to-review
    list_res = await TransactionalQueueService.get_queue_emails(
        session=db_session, queue_type="ready-to-review", store_id=store.id, page=1, limit=10
    )
    assert list_res.total == 1
    assert len(list_res.items) == 1
    assert list_res.items[0].id == e1.id
    assert list_res.items[0].subject == "Subject 1"
