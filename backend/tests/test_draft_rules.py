"""Unit and Integration tests for Intent-Based Draft Rules & Eligibility Matrix.

Tests compliance with Invariants R-22, R-23, R-08, and R-21.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from app.db.models.draft import ReplyDraftVersion
from app.db.models.email import IncomingEmail
from app.db.models.shopify import ShopifyProductSnapshot
from app.db.models.store import Mailbox, StoreProfile
from app.draft.service import DraftService
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_product_inquiry_eligibility_and_warnings(db_session: AsyncSession):
    """Product inquiries generate drafts; unresolved products trigger PRODUCT_NOT_RESOLVED warning."""
    # 1. Product inquiry with resolved product
    email_resolved = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=101,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="classified",
        classification_category="product_inquiry",
        intent_confidence=0.95,
        customer_status="no_order",
    )
    prod_snap_resolved = ShopifyProductSnapshot(
        id=uuid.uuid4(),
        store_profile_id=email_resolved.store_profile_id,
        incoming_email_id=email_resolved.id,
        search_query="Leather Jacket",
        raw_query_terms=["Leather", "Jacket"],
        matched_count=1,
        product_resolved=True,
        matched_products=[{"title": "Vintage Leather Jacket", "min_price": "89.50"}],
        execution_time_ms=50,
    )
    email_resolved.product_snapshots = [prod_snap_resolved]

    eligible, reason, warnings = DraftService.evaluate_draft_eligibility(email_resolved)
    assert eligible is True
    assert reason is None
    assert "PRODUCT_NOT_RESOLVED" not in warnings

    # 2. Product inquiry with UNRESOLVED product (Invariant R-16)
    email_unresolved = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=102,
        uidvalidity=1,
        sender_email="customer2@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="classified",
        classification_category="product_inquiry",
        intent_confidence=0.90,
        customer_status="no_order",
    )
    prod_snap_unresolved = ShopifyProductSnapshot(
        id=uuid.uuid4(),
        store_profile_id=email_unresolved.store_profile_id,
        incoming_email_id=email_unresolved.id,
        search_query="Mystery Shoe",
        raw_query_terms=["Mystery", "Shoe"],
        matched_count=0,
        product_resolved=False,
        matched_products=[],
        execution_time_ms=40,
    )
    email_unresolved.product_snapshots = [prod_snap_unresolved]

    eligible2, reason2, warnings2 = DraftService.evaluate_draft_eligibility(email_unresolved)
    assert eligible2 is True
    assert "PRODUCT_NOT_RESOLVED" in warnings2


@pytest.mark.asyncio
async def test_order_support_eligibility_and_warnings(db_session: AsyncSession):
    """Order support queries generate drafts; missing recent order triggers NO_RECENT_ORDER."""
    # Has order
    email_with_order = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=103,
        uidvalidity=1,
        sender_email="buyer@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="classified",
        classification_category="order_support",
        intent_confidence=0.92,
        customer_status="has_order_record",
    )
    eligible1, _, warnings1 = DraftService.evaluate_draft_eligibility(email_with_order)
    assert eligible1 is True
    assert "NO_RECENT_ORDER" not in warnings1

    # No order in 60 days
    email_no_order = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=104,
        uidvalidity=1,
        sender_email="unknown@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="classified",
        classification_category="order_support",
        intent_confidence=0.88,
        customer_status="no_order",
    )
    eligible2, _, warnings2 = DraftService.evaluate_draft_eligibility(email_no_order)
    assert eligible2 is True
    assert "NO_RECENT_ORDER" in warnings2


@pytest.mark.asyncio
async def test_complaint_and_refund_warning_badges(db_session: AsyncSession):
    """Complaints and return/refund intents always trigger mandatory warning badges."""
    email_complaint = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=105,
        uidvalidity=1,
        sender_email="angry@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="classified",
        classification_category="complaint",
        intent_confidence=0.99,
        customer_status="has_order_record",
    )
    eligible1, _, warnings1 = DraftService.evaluate_draft_eligibility(email_complaint)
    assert eligible1 is True
    assert "COMPLAINT_DETECTED" in warnings1

    email_refund = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=106,
        uidvalidity=1,
        sender_email="refund@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="classified",
        classification_category="return_or_refund",
        intent_confidence=0.95,
        customer_status="has_order_record",
    )
    eligible2, _, warnings2 = DraftService.evaluate_draft_eligibility(email_refund)
    assert eligible2 is True
    assert "RETURN_OR_REFUND_REQUESTED" in warnings2


@pytest.mark.asyncio
async def test_spam_isolated_and_blocked_from_drafts(db_session: AsyncSession):
    """Spam emails must NEVER generate drafts (Invariant R-08)."""
    email_spam = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=107,
        uidvalidity=1,
        sender_email="spammer@phishing.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="spam",
        spam_status="spam",
        classification_category="spam",
    )
    eligible, reason, _ = DraftService.evaluate_draft_eligibility(email_spam)
    assert eligible is False
    assert reason == "SPAM_DETECTED"


@pytest.mark.asyncio
async def test_non_drafting_intents_and_prompt_injection(db_session: AsyncSession):
    """Partnership, other, uncertain intents and prompt injection route to manual review."""
    for bad_intent in ("partnership", "other", "uncertain"):
        email = IncomingEmail(
            id=uuid.uuid4(),
            store_profile_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            imap_uid=108,
            uidvalidity=1,
            sender_email="partner@collab.com",
            recipient_email="support@store.com",
            received_at=datetime.datetime.now(datetime.UTC),
            status="classified",
            classification_category=bad_intent,
            intent_confidence=0.90,
        )
        eligible, reason, _ = DraftService.evaluate_draft_eligibility(email)
        assert eligible is False
        assert reason is not None
        assert bad_intent.upper() in reason

    # Prompt injection
    email_pi = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=109,
        uidvalidity=1,
        sender_email="hacker@jailbreak.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="manual_review",
        classification_category="product_inquiry",
        review_reason_code="PROMPT_INJECTION_DETECTED",
    )
    eligible_pi, reason_pi, _ = DraftService.evaluate_draft_eligibility(email_pi)
    assert eligible_pi is False
    assert reason_pi == "PROMPT_INJECTION_DETECTED"


@pytest.mark.asyncio
async def test_attachment_and_shopify_errors_route_to_manual_review(db_session: AsyncSession):
    """Attachment errors (R-21) and Shopify failures must not generate hallucinated drafts."""
    email_att = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=110,
        uidvalidity=1,
        sender_email="user@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="manual_review",
        classification_category="order_support",
        manual_review_reason="ATTACHMENT_FAILED",
    )
    eligible_att, reason_att, _ = DraftService.evaluate_draft_eligibility(email_att)
    assert eligible_att is False
    assert reason_att == "ATTACHMENT_FAILED"

    email_shopify = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        imap_uid=111,
        uidvalidity=1,
        sender_email="user@example.com",
        recipient_email="support@store.com",
        received_at=datetime.datetime.now(datetime.UTC),
        status="manual_review",
        classification_category="order_support",
        manual_review_reason="SHOPIFY_LOOKUP_FAILED",
    )
    eligible_sh, reason_sh, _ = DraftService.evaluate_draft_eligibility(email_shopify)
    assert eligible_sh is False
    assert reason_sh == "SHOPIFY_LOOKUP_FAILED"


@pytest.mark.asyncio
async def test_generate_initial_draft_full_integration(db_session: AsyncSession):
    """Generates initial draft V1 in database, updates email status to pending_approval."""
    now_utc = datetime.datetime.now(datetime.UTC)
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US",
        brand_name="Wrydeco US",
        public_domain="wrydeco.com",
        status="active",
        default_language="en",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password="enc_password",
    )
    db_session.add_all([store, mailbox])
    await db_session.flush()

    email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=201,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Where is my jacket?",
        received_at=now_utc,
        status="classified",
        classification_category="product_inquiry",
        intent_confidence=0.95,
        customer_status="no_order",
    )
    db_session.add(email)
    await db_session.commit()

    # Generate draft
    draft = await DraftService.generate_initial_draft(db_session, email.id)
    assert draft is not None
    assert draft.current_version_number == 1
    assert draft.status == "pending_approval"

    # Reload email
    refreshed_email = await db_session.get(IncomingEmail, email.id)
    assert refreshed_email is not None
    assert refreshed_email.status == "pending_approval"
    assert refreshed_email.current_draft_version == 1

    # Verify immutable version 1 record
    version = await db_session.get(ReplyDraftVersion, draft.current_version_id)
    assert version is not None
    assert version.version_number == 1
    assert version.source == "ai"
    assert version.is_current_version is True
    assert version.subject.startswith("Re:")
    assert "Wrydeco US" in version.body_text
