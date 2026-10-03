"""Unit and integration tests for Retention Service and Invariant R-34."""

from __future__ import annotations

import datetime
import uuid

import pytest
from app.config import Settings
from app.db.models.audit import AuditEvent
from app.db.models.draft import (
    ReplyDeliveryAttempt,
    ReplyDraft,
    ReplyDraftVersion,
)
from app.db.models.email import (
    EmailAttachmentMetadata,
    EmailClassification,
    EmailJob,
    IncomingEmail,
)
from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
from app.db.models.store import Mailbox, ProxyProfile, StoreProfile
from app.retention.service import RetentionService
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def test_config_retention_days_invariant_r34():
    """Validates Invariant R-34: RETENTION_DAYS strictly cannot exceed 120."""
    # 120 is maximum allowed
    s120 = Settings(RETENTION_DAYS=120)
    assert s120.RETENTION_DAYS == 120

    # Lower retention values are permitted
    s30 = Settings(RETENTION_DAYS=30)
    assert s30.RETENTION_DAYS == 30

    # Values > 120 are strictly rejected (Invariant R-34)
    with pytest.raises(ValidationError):
        Settings(RETENTION_DAYS=121)

    with pytest.raises(ValidationError):
        Settings(RETENTION_DAYS=365)

    # Values < 1 are rejected
    with pytest.raises(ValidationError):
        Settings(RETENTION_DAYS=0)


def test_calculate_cutoff_date():
    """Validates calculate_cutoff_date clamps between 1 and 120 days."""
    now_utc = datetime.datetime.now(datetime.UTC)
    cutoff = RetentionService.calculate_cutoff_date(120)
    diff_days = (now_utc - cutoff).total_seconds() / 86400.0
    assert 119.9 <= diff_days <= 120.1

    # Over 120 is clamped to 120
    cutoff_over = RetentionService.calculate_cutoff_date(200)
    diff_over = (now_utc - cutoff_over).total_seconds() / 86400.0
    assert 119.9 <= diff_over <= 120.1


@pytest.mark.asyncio
async def test_dry_run_and_live_retention_cleanup(db_session: AsyncSession, bootstrap_user):
    """Verifies dry-run counts without deleting, and live deletion deletes only expired records."""
    admin_user = await bootstrap_user("retention_admin", "AdminPass123!")
    now_utc = datetime.datetime.now(datetime.UTC)
    old_time = now_utc - datetime.timedelta(days=130)
    fresh_time = now_utc - datetime.timedelta(days=10)

    # 1. Seed Store and Mailbox (Must NOT be deleted per R-34)
    proxy = ProxyProfile(
        id=uuid.uuid4(),
        name="Retention Test Proxy",
        host="192.168.1.1",
        port=1080,
    )
    db_session.add(proxy)

    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco Retention Store",
        brand_name="Wrydeco",
        public_domain="wrydeco.com",
    )
    db_session.add(store)
    await db_session.flush()

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password="enc_pass_test",
    )
    db_session.add(mailbox)
    await db_session.flush()

    # 2. Seed Expired Incoming Email (130 days old)
    old_email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=101,
        uidvalidity=1,
        sender_email="old_customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Old Inquiry",
        received_at=old_time,
        created_at=old_time,
        status="pending_approval",
    )
    db_session.add(old_email)
    await db_session.flush()

    # Expired subordinate entities
    old_draft = ReplyDraft(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        store_profile_id=store.id,
        created_at=old_time,
    )
    db_session.add(old_draft)
    await db_session.flush()

    old_version = ReplyDraftVersion(
        id=uuid.uuid4(),
        draft_id=old_draft.id,
        incoming_email_id=old_email.id,
        version_number=1,
        subject="Re: Old Inquiry",
        body_text="Old response",
        body_html="<p>Old response</p>",
        content_hash="abc123hash",
        created_at=old_time,
    )
    db_session.add(old_version)
    await db_session.flush()

    old_draft.current_version_id = old_version.id

    old_attempt = ReplyDeliveryAttempt(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        draft_id=old_draft.id,
        draft_version_id=old_version.id,
        idempotency_key="idemp_old_1",
        approved_by=admin_user.id,
        approved_at=old_time,
        created_at=old_time,
    )
    db_session.add(old_attempt)

    old_order_snap = ShopifyOrderSnapshot(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        store_profile_id=store.id,
        customer_email="old_customer@example.com",
        lookup_status="no_order",
        lookup_checked_at=old_time,
        created_at=old_time,
    )
    db_session.add(old_order_snap)

    old_product_snap = ShopifyProductSnapshot(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        store_profile_id=store.id,
        search_query="poster",
        raw_query_terms=["poster"],
        fetched_at=old_time,
    )
    db_session.add(old_product_snap)

    old_class = EmailClassification(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        intent="product_inquiry",
        created_at=old_time,
    )
    db_session.add(old_class)

    old_attach = EmailAttachmentMetadata(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        filename="old.jpg",
        content_type="image/jpeg",
        size_bytes=1024,
        sha256_hash="hasholdattach",
        created_at=old_time,
    )
    db_session.add(old_attach)

    old_job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=old_email.id,
        store_profile_id=store.id,
        job_type="classify",
        status="completed",
        created_at=old_time,
    )
    db_session.add(old_job)

    old_audit = AuditEvent(
        id=uuid.uuid4(),
        actor_user_id=admin_user.id,
        event_type="OLD_USER_LOGIN",
        created_at=old_time,
    )
    db_session.add(old_audit)

    # 3. Seed Fresh Incoming Email (10 days old - Must NOT be deleted)
    fresh_email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=202,
        uidvalidity=1,
        sender_email="fresh_customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Fresh Inquiry",
        received_at=fresh_time,
        created_at=fresh_time,
        status="pending_approval",
    )
    db_session.add(fresh_email)
    await db_session.commit()

    # --- TEST DRY RUN ---
    dry_report = await RetentionService.run_cleanup_job(
        db=db_session,
        retention_days=120,
        dry_run=True,
    )
    assert dry_report.dry_run is True
    assert dry_report.deleted_counts.incoming_emails == 1
    assert dry_report.deleted_counts.reply_drafts == 1
    assert dry_report.deleted_counts.reply_draft_versions == 1
    assert dry_report.deleted_counts.reply_delivery_attempts == 1
    assert dry_report.deleted_counts.shopify_order_snapshots == 1
    assert dry_report.deleted_counts.shopify_product_snapshots == 1
    assert dry_report.deleted_counts.email_classifications == 1
    assert dry_report.deleted_counts.email_attachment_metadata == 1
    assert dry_report.deleted_counts.email_jobs == 1
    assert dry_report.deleted_counts.audit_events == 1

    # Confirm records still exist in DB after dry-run
    chk_old = await db_session.execute(select(IncomingEmail).where(IncomingEmail.id == old_email.id))
    assert chk_old.scalar_one_or_none() is not None

    # --- TEST LIVE DELETION ---
    live_report = await RetentionService.run_cleanup_job(
        db=db_session,
        retention_days=120,
        dry_run=False,
        actor_user_id=admin_user.id,
    )
    assert live_report.dry_run is False
    assert live_report.deleted_counts.incoming_emails == 1
    assert live_report.total_records_affected >= 10

    # Old email and its subordinates must be GONE
    res_old = await db_session.execute(select(IncomingEmail).where(IncomingEmail.id == old_email.id))
    assert res_old.scalar_one_or_none() is None

    res_draft = await db_session.execute(select(ReplyDraft).where(ReplyDraft.id == old_draft.id))
    assert res_draft.scalar_one_or_none() is None

    res_audit_old = await db_session.execute(select(AuditEvent).where(AuditEvent.id == old_audit.id))
    assert res_audit_old.scalar_one_or_none() is None

    # Fresh email (10 days old) MUST BE PRESERVED
    res_fresh = await db_session.execute(select(IncomingEmail).where(IncomingEmail.id == fresh_email.id))
    assert res_fresh.scalar_one_or_none() is not None

    # Store, proxy, mailbox, user MUST BE PRESERVED (Invariant R-34)
    res_store = await db_session.execute(select(StoreProfile).where(StoreProfile.id == store.id))
    assert res_store.scalar_one_or_none() is not None

    res_proxy = await db_session.execute(select(ProxyProfile).where(ProxyProfile.id == proxy.id))
    assert res_proxy.scalar_one_or_none() is not None

    res_mb = await db_session.execute(select(Mailbox).where(Mailbox.id == mailbox.id))
    assert res_mb.scalar_one_or_none() is not None

    # Retention audit event MUST BE CREATED
    res_ret_audit = await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "RETENTION_CLEANUP_EXECUTED")
    )
    ret_audit = res_ret_audit.scalar_one_or_none()
    assert ret_audit is not None
    assert ret_audit.safe_change_summary is not None
    assert ret_audit.safe_change_summary.get("retention_days") == 120


@pytest.mark.asyncio
async def test_retention_api_endpoints(client, bootstrap_user, db_session: AsyncSession):
    """Tests POST /api/system/retention/run and GET /api/system/retention/status."""
    await bootstrap_user("retention_api_user", "MyPassword123!")

    # Login to acquire session
    login_res = await client.post(
        "/api/auth/login",
        json={"username": "retention_api_user", "password": "MyPassword123!"},
    )
    assert login_res.status_code == 200

    # 1. Trigger dry-run via API
    run_res = await client.post(
        "/api/system/retention/run",
        json={"retention_days": 120, "dry_run": True, "batch_size": 200},
    )
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["dry_run"] is True
    assert run_data["retention_days"] == 120
    assert "deleted_counts" in run_data

    # 2. Query retention status
    status_res = await client.get("/api/system/retention/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["configured_retention_days"] <= 120
    assert "current_cutoff_date" in status_data
    assert "pending_expired_emails_count" in status_data
