"""Tests for Stale Draft Detection and Warning Pipeline.

INVARIANT R-18:
- Draft metadata persists policy_hashes_used.
- Operator review / ingestion fetch verifies hash freshness against current effective policies.
- If any policy was updated, sets is_stale=True, stale_reason="POLICY_UPDATED",
  and records stale_details to trigger UI banner with "Regenerate Draft" button.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.core.crypto import encrypt_secret
from app.db.models.email import IncomingEmail
from app.db.models.store import Mailbox, StorePolicy, StoreProfile
from app.ingestion.service import MailFetchService
from app.shopify.service import ShopifyService, _memory_policy_cache
from sqlalchemy.ext.asyncio import AsyncSession


def test_check_draft_policy_freshness_when_hashes_match() -> None:
    """When policies have not changed since draft generation, draft is fresh."""
    policy_hashes_used = {
        "REFUND": "hash_refund_v1",
        "SHIPPING": "hash_shipping_v1",
    }
    current_hashes = {
        "REFUND": "hash_refund_v1",
        "SHIPPING": "hash_shipping_v1",
        "TERMS": "hash_terms_v1",
    }

    res = ShopifyService.check_draft_policy_freshness(
        policy_hashes_used=policy_hashes_used,
        current_policy_hashes=current_hashes,
    )

    assert res.is_stale is False
    assert res.stale_reason is None
    assert res.stale_details == []


def test_check_draft_policy_freshness_detects_updated_policy() -> None:
    """INVARIANT R-18: If any policy content changed, draft must be flagged stale with details."""
    policy_hashes_used = {
        "REFUND": "hash_refund_v1",
        "SHIPPING": "hash_shipping_v1",
    }
    current_hashes = {
        "REFUND": "hash_refund_v2_UPDATED",
        "SHIPPING": "hash_shipping_v1",
    }

    res = ShopifyService.check_draft_policy_freshness(
        policy_hashes_used=policy_hashes_used,
        current_policy_hashes=current_hashes,
    )

    assert res.is_stale is True
    assert res.stale_reason == "POLICY_UPDATED"
    assert len(res.stale_details) == 1
    assert res.stale_details[0]["policy_type"] == "REFUND"
    assert res.stale_details[0]["used_hash"] == "hash_refund_v1"
    assert res.stale_details[0]["current_hash"] == "hash_refund_v2_UPDATED"


def test_check_draft_policy_freshness_when_no_hashes_used() -> None:
    """Drafts generated without policy dependencies are not stale."""
    res_none = ShopifyService.check_draft_policy_freshness(
        policy_hashes_used=None,
        current_policy_hashes={"REFUND": "h1"},
    )
    assert res_none.is_stale is False

    res_empty = ShopifyService.check_draft_policy_freshness(
        policy_hashes_used={},
        current_policy_hashes={"REFUND": "h1"},
    )
    assert res_empty.is_stale is False


@pytest.mark.asyncio
async def test_fetch_email_content_detects_stale_draft_and_persists_flag(
    db_session: AsyncSession,
) -> None:
    """Full integration: fetching email content detects outdated policy hashes and marks DB record stale."""
    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name=f"test-store-stale-{uuid.uuid4().hex[:6]}",
        brand_name="Stale Draft Test Store",
        public_domain="wrydeco.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password=encrypt_secret("fake_imap_pass"),
    )
    db_session.add(mailbox)

    # Effective current refund policy in store DB
    current_refund_policy = StorePolicy(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        policy_type="REFUND",
        title="Refund Policy",
        body_text="All items can be returned in 14 days.",
        content_hash="current_valid_sha256_hash_14_days",
        is_custom=False,
    )
    db_session.add(current_refund_policy)

    # Email that was drafted using an OLD 30-day refund policy hash
    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Can I return my order?",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=3001,
        uidvalidity=1,
        status="pending_approval",
        policy_hashes_used={"REFUND": "old_obsolete_sha256_hash_30_days"},
        is_stale=False,
    )
    db_session.add(email_rec)
    await db_session.commit()

    # Clear memory cache for this store to ensure fresh DB read
    _memory_policy_cache.pop(store_id, None)

    # Mock IMAP peek fetch
    fake_raw_email = (
        b"From: customer@example.com\r\n"
        b"To: support@wrydeco.com\r\n"
        b"Subject: Can I return my order?\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Hello, I want to know about your return policy."
    )

    with patch("app.ingestion.service.IMAPClient.fetch_raw_email_peek", new_callable=AsyncMock) as mock_peek:
        mock_peek.return_value = fake_raw_email
        resp = await MailFetchService.fetch_email_content(db=db_session, email_id=email_rec.id)

    # 1. API Response flags
    assert resp.is_stale is True
    assert resp.stale_reason == "POLICY_UPDATED"
    assert resp.stale_details is not None
    assert len(resp.stale_details) == 1
    assert resp.stale_details[0]["policy_type"] == "REFUND"
    assert resp.stale_details[0]["used_hash"] == "old_obsolete_sha256_hash_30_days"
    assert resp.stale_details[0]["current_hash"] == "current_valid_sha256_hash_14_days"

    # 2. Database Record Persistence
    await db_session.refresh(email_rec)
    assert email_rec.is_stale is True
    assert email_rec.stale_reason == "POLICY_UPDATED"
    assert email_rec.stale_details is not None
    assert email_rec.stale_details[0]["policy_type"] == "REFUND"
