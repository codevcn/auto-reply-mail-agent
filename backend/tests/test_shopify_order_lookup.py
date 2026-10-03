"""Tests for 60-day Shopify Order Lookup, 5 Status Flags Classification, and Error Retry Pipeline.

Requirements:
- Invariant R-09: 60-day order window, normalized email, test orders excluded.
- Invariant R-10: 5 independent status flags, customer relation preserved on cancelled/refunded.
- Invariant R-11: Explicit distinction between lookup_unavailable and no_order.
- Section 14.1: Exponential retry schedule [1m, 5m, 15m, 30m, 60m] and terminal error handling.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.db.models.email import EmailJob, IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.queue.service import (
    TransactionalQueueService,
)
from app.shopify.exceptions import ShopifyAuthError, ShopifyProxyError, ShopifyTransientError
from app.shopify.service import ShopifyService
from sqlalchemy.ext.asyncio import AsyncSession


def test_normalize_email_strictly_trim_and_lowercase_only() -> None:
    # Sub-addressing and dots must be preserved (Invariant R-09)
    raw = "  Customer.Name+Promo@EXAMPLE.com  "
    normalized = ShopifyService.normalize_email(raw)
    assert normalized == "customer.name+promo@example.com"
    assert "+" in normalized
    assert "." in normalized


def test_classify_order_flags_all_active_order_within_60_days() -> None:
    now_utc = datetime.datetime.now(datetime.UTC)
    orders = [
        {
            "id": "ord_1",
            "created_at": (now_utc - datetime.timedelta(days=10)).isoformat(),
            "financial_status": "paid",
            "fulfillment_status": "unfulfilled",
            "test": False,
        }
    ]
    flags = ShopifyService.classify_order_flags(orders, window_days=60, reference_time=now_utc)
    assert flags.has_order_record is True
    assert flags.has_recent_order is True
    assert flags.recent_orders_count == 1
    assert flags.has_paid_order is True
    assert flags.has_active_order is True
    assert flags.has_cancelled_order is False
    assert flags.has_refunded_order is False
    assert flags.has_fulfilled_order is False


def test_classify_order_flags_preserves_customer_relationship_on_cancelled_and_refunded() -> None:
    """INVARIANT R-10: Cancelled or refunded orders preserve customer flags."""
    now_utc = datetime.datetime.now(datetime.UTC)
    orders = [
        {
            "id": "ord_cancelled",
            "created_at": (now_utc - datetime.timedelta(days=5)).isoformat(),
            "financial_status": "refunded",
            "cancelled_at": (now_utc - datetime.timedelta(days=4)).isoformat(),
            "test": False,
        }
    ]
    flags = ShopifyService.classify_order_flags(orders, window_days=60, reference_time=now_utc)
    assert flags.has_order_record is True, "Must maintain customer record!"
    assert flags.has_recent_order is True
    assert flags.has_cancelled_order is True
    assert flags.has_refunded_order is True
    assert flags.has_active_order is False


def test_classify_order_flags_excludes_orders_older_than_60_days() -> None:
    """Orders older than 60 days maintain order record but are excluded from recent order count."""
    now_utc = datetime.datetime.now(datetime.UTC)
    orders = [
        {
            "id": "ord_old",
            "created_at": (now_utc - datetime.timedelta(days=65)).isoformat(),
            "financial_status": "paid",
            "test": False,
        }
    ]
    flags = ShopifyService.classify_order_flags(orders, window_days=60, reference_time=now_utc)
    assert flags.has_order_record is True, "Has past order record"
    assert flags.has_recent_order is False, "Outside 60-day window"
    assert flags.recent_orders_count == 0


def test_classify_order_flags_excludes_test_orders() -> None:
    """INVARIANT R-09: Test orders must be completely excluded."""
    now_utc = datetime.datetime.now(datetime.UTC)
    orders = [
        {
            "id": "ord_test",
            "created_at": (now_utc - datetime.timedelta(days=2)).isoformat(),
            "financial_status": "paid",
            "test": True,
        }
    ]
    flags = ShopifyService.classify_order_flags(orders, window_days=60, reference_time=now_utc)
    assert flags.has_order_record is False
    assert flags.has_recent_order is False
    assert flags.recent_orders_count == 0


@pytest.mark.asyncio
async def test_enrich_order_lookup_emits_no_order_on_zero_matches() -> None:
    """INVARIANT R-11: 'no_order' emitted only on successful lookup with 0 orders."""
    mock_client = AsyncMock()
    with patch.object(ShopifyService, "lookup_recent_orders", return_value=[]):
        res = await ShopifyService.enrich_order_lookup(mock_client, "buyer@example.com", days=60)
        assert res.status == "no_order"
        assert res.flags.has_order_record is False
        assert res.flags.recent_orders_count == 0


@pytest.mark.asyncio
async def test_queue_order_enrichment_transient_error_retry_schedule(db_session: AsyncSession) -> None:
    """INVARIANT R-11 & Section 14.1: Transient errors set lookup_unavailable and schedule retries [1m, 5m, 15m, 30m, 60m]."""
    # Setup test store profile and incoming email
    store = StoreProfile(
        id=uuid.uuid4(),
        name=f"test-store-{uuid.uuid4().hex[:6]}",
        brand_name="Test Store",
        public_domain="test.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@test.com",
        encrypted_password="enc",
    )
    db_session.add(mailbox)

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer@example.com",
        recipient_email="support@test.com",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=101,
        uidvalidity=1,
        status="classified",
        classification_category="order_support",
    )
    db_session.add(email_rec)

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_rec.id,
        store_profile_id=store.id,
        job_type="enrich_order",
        status="processing",
        attempts=1,  # First attempt
    )
    db_session.add(job)
    await db_session.commit()

    # Simulate ShopifyProxyError during enrichment
    with patch.object(
        TransactionalQueueService,
        "_get_shopify_client_for_store",
        return_value=AsyncMock(),
    ), patch.object(
        ShopifyService,
        "enrich_order_lookup",
        side_effect=ShopifyProxyError("SOCKS5 tunnel disconnected"),
    ):
        result = await TransactionalQueueService.process_order_enrichment_job(db_session, job.id)
        assert result is False

    await db_session.refresh(job)
    await db_session.refresh(email_rec)

    # Invariant R-11: MUST be lookup_unavailable, NEVER no_order
    assert email_rec.customer_status == "lookup_unavailable"
    assert job.status == "queued"
    # Delay for attempt 1 is 60 seconds
    assert job.scheduled_at is not None
    now_utc = datetime.datetime.now(datetime.UTC)
    diff_sec = (job.scheduled_at - now_utc).total_seconds()
    assert 50 <= diff_sec <= 65


@pytest.mark.asyncio
async def test_queue_order_enrichment_retry_exhaustion_moves_to_manual_review(db_session: AsyncSession) -> None:
    """Section 14.1: Exhausting 5 retries transitions email to manual_review with SHOPIFY_LOOKUP_FAILED."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name=f"test-store-{uuid.uuid4().hex[:6]}",
        brand_name="Test Store",
        public_domain="test.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@test.com",
        encrypted_password="enc",
    )
    db_session.add(mailbox)

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer@example.com",
        recipient_email="support@test.com",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=102,
        uidvalidity=1,
        status="classified",
        classification_category="order_support",
    )
    db_session.add(email_rec)

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_rec.id,
        store_profile_id=store.id,
        job_type="enrich_order",
        status="processing",
        attempts=5,  # 5th attempt exhausted
    )
    db_session.add(job)
    await db_session.commit()

    with patch.object(
        TransactionalQueueService,
        "_get_shopify_client_for_store",
        return_value=AsyncMock(),
    ), patch.object(
        ShopifyService,
        "enrich_order_lookup",
        side_effect=ShopifyTransientError("Shopify 500 Internal Error"),
    ):
        result = await TransactionalQueueService.process_order_enrichment_job(db_session, job.id)
        assert result is False

    await db_session.refresh(job)
    await db_session.refresh(email_rec)

    assert job.status == "failed"
    assert email_rec.status == "manual_review"
    assert email_rec.review_reason_code == "SHOPIFY_LOOKUP_FAILED"
    assert email_rec.customer_status == "lookup_unavailable"


@pytest.mark.asyncio
async def test_queue_order_enrichment_terminal_auth_error_halts_immediately(db_session: AsyncSession) -> None:
    """Section 14.1: Terminal 401/403 errors halt retries immediately (0 retries)."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name=f"test-store-{uuid.uuid4().hex[:6]}",
        brand_name="Test Store",
        public_domain="test.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@test.com",
        encrypted_password="enc",
    )
    db_session.add(mailbox)

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer@example.com",
        recipient_email="support@test.com",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=103,
        uidvalidity=1,
        status="classified",
        classification_category="order_support",
    )
    db_session.add(email_rec)

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_rec.id,
        store_profile_id=store.id,
        job_type="enrich_order",
        status="processing",
        attempts=1,
    )
    db_session.add(job)
    await db_session.commit()

    with patch.object(
        TransactionalQueueService,
        "_get_shopify_client_for_store",
        return_value=AsyncMock(),
    ), patch.object(
        ShopifyService,
        "enrich_order_lookup",
        side_effect=ShopifyAuthError("401 Unauthorized"),
    ):
        result = await TransactionalQueueService.process_order_enrichment_job(db_session, job.id)
        assert result is False

    await db_session.refresh(job)
    await db_session.refresh(email_rec)

    assert job.status == "failed"
    assert email_rec.status == "manual_review"
    assert email_rec.review_reason_code == "SHOPIFY_AUTH_FAILED"
    assert email_rec.customer_status == "lookup_unavailable"
