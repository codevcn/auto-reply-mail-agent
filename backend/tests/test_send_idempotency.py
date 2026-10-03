"""Unit and Integration tests for Send Idempotency and Zero Autonomous Sending.

Tests compliance with Invariants R-01 and R-26.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.db.models.draft import ReplyDeliveryAttempt, ReplyDraft, ReplyDraftVersion
from app.db.models.email import IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.db.models.user import User
from app.delivery.service import DeliveryBusinessError, DeliveryService
from app.mail.schemas import SMTPDeliveryResult
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_send_idempotency_key_replays_sent_result(db_session: AsyncSession):
    """Calling approve_and_send with same idempotency_key replays result without double-sending (Invariant R-26)."""
    now_utc = datetime.datetime.now(datetime.UTC)
    user = User(
        id=uuid.uuid4(),
        username="operator_idemp",
        normalized_username="operator_idemp",
        password_hash="hash",
        status="active",
    )
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US",
        brand_name="Wrydeco US",
        public_domain="wrydeco.com",
        status="active",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password="enc_password",
    )
    db_session.add_all([user, store, mailbox])
    await db_session.flush()

    email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=501,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Shipping time",
        received_at=now_utc,
        status="pending_approval",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        incoming_email_id=email.id,
        store_profile_id=store.id,
        current_version_number=1,
        status="pending_approval",
    )
    db_session.add_all([email, draft])
    await db_session.flush()

    version = ReplyDraftVersion(
        id=uuid.uuid4(),
        draft_id=draft.id,
        incoming_email_id=email.id,
        version_number=1,
        subject="Re: Shipping time",
        body_text="Standard shipping takes 3-5 days.",
        body_html="<p>Standard shipping takes 3-5 days.</p>",
        language="en",
        source="ai",
        content_hash="abc",
        is_current_version=True,
    )
    db_session.add(version)
    await db_session.flush()
    draft.current_version_id = version.id
    await db_session.commit()

    idempotency_key = str(uuid.uuid4())
    smtp_ok = SMTPDeliveryResult(status="sent", response_code=250, response_text="250 2.0.0 Ok")

    with (
        patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp,
        patch("app.delivery.service.IMAPClient.append_to_sent_folder", new_callable=AsyncMock) as mock_imap,
    ):
        mock_smtp.return_value = smtp_ok
        mock_imap.return_value = True

        # First dispatch
        res1 = await DeliveryService.approve_and_send(
            session=db_session,
            email_id=email.id,
            idempotency_key=idempotency_key,
            user_id=user.id,
        )
        assert res1.success is True
        assert res1.status == "sent"
        assert mock_smtp.call_count == 1

        # Second dispatch with identical idempotency_key (simulating double click or client retry)
        res2 = await DeliveryService.approve_and_send(
            session=db_session,
            email_id=email.id,
            idempotency_key=idempotency_key,
            user_id=user.id,
        )
        # Idempotent replay: Returned previous success, SMTP was NOT called again!
        assert res2.success is True
        assert res2.status == "sent"
        assert res2.outgoing_message_id == res1.outgoing_message_id
        assert mock_smtp.call_count == 1


@pytest.mark.asyncio
async def test_concurrency_protection_blocks_in_flight_send(db_session: AsyncSession):
    """When a send is currently in progress, concurrent requests with same key get 409 Conflict."""
    now_utc = datetime.datetime.now(datetime.UTC)
    user = User(
        id=uuid.uuid4(),
        username="operator_concurrent",
        normalized_username="operator_concurrent",
        password_hash="hash",
        status="active",
    )
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US Concurrency",
        brand_name="Wrydeco US",
        public_domain="wrydeco.com",
        status="active",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support_conc@wrydeco.com",
        encrypted_password="enc_password",
    )
    db_session.add_all([user, store, mailbox])
    await db_session.flush()

    email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=502,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Shipping time",
        received_at=now_utc,
        status="pending_approval",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        incoming_email_id=email.id,
        store_profile_id=store.id,
        current_version_number=1,
        status="pending_approval",
    )
    db_session.add_all([email, draft])
    await db_session.flush()

    version = ReplyDraftVersion(
        id=uuid.uuid4(),
        draft_id=draft.id,
        incoming_email_id=email.id,
        version_number=1,
        subject="Re: Shipping time",
        body_text="Standard shipping takes 3-5 days.",
        body_html="<p>Standard shipping takes 3-5 days.</p>",
        language="en",
        source="ai",
        content_hash="abc",
        is_current_version=True,
    )
    db_session.add(version)
    await db_session.flush()
    draft.current_version_id = version.id
    await db_session.commit()

    email_id = email.id
    user_id = user.id
    idemp_key = str(uuid.uuid4())

    existing_attempt = ReplyDeliveryAttempt(
        id=uuid.uuid4(),
        incoming_email_id=email.id,
        draft_id=draft.id,
        draft_version_id=version.id,
        idempotency_key=idemp_key,
        approved_by=user.id,
        approved_at=now_utc,
        status="sending",  # In-flight
        outgoing_message_id="<test@store.com>",
    )
    db_session.add(existing_attempt)
    await db_session.commit()

    with pytest.raises(DeliveryBusinessError) as exc_info:
        await DeliveryService.approve_and_send(
            session=db_session,
            email_id=email_id,
            idempotency_key=idemp_key,
            user_id=user_id,
        )
    assert exc_info.value.code == "SEND_IN_PROGRESS"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_deterministic_smtp_failure_rolls_back_to_pending_approval(db_session: AsyncSession):
    """Failure before DATA phase rolls back status to pending_approval so operator can retry."""
    now_utc = datetime.datetime.now(datetime.UTC)
    user = User(
        id=uuid.uuid4(),
        username="operator_fail",
        normalized_username="operator_fail",
        password_hash="hash",
        status="active",
    )
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US Fail",
        brand_name="Wrydeco US",
        public_domain="wrydeco.com",
        status="active",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support_fail@wrydeco.com",
        encrypted_password="enc_password",
    )
    db_session.add_all([user, store, mailbox])
    await db_session.flush()

    email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=502,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Query",
        received_at=now_utc,
        status="pending_approval",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        incoming_email_id=email.id,
        store_profile_id=store.id,
        current_version_number=1,
        status="pending_approval",
    )
    db_session.add_all([email, draft])
    await db_session.flush()

    version = ReplyDraftVersion(
        id=uuid.uuid4(),
        draft_id=draft.id,
        incoming_email_id=email.id,
        version_number=1,
        subject="Re: Query",
        body_text="Body",
        body_html="<p>Body</p>",
        language="en",
        source="ai",
        content_hash="abc",
        is_current_version=True,
    )
    db_session.add(version)
    await db_session.flush()
    draft.current_version_id = version.id
    await db_session.commit()

    # SMTP failure before DATA (e.g. Auth failure)
    smtp_fail = SMTPDeliveryResult(status="failed", error_code="SMTP_AUTH_FAILED", error_detail="Invalid credentials")

    with patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp:
        mock_smtp.return_value = smtp_fail

        with pytest.raises(DeliveryBusinessError) as exc_info:
            await DeliveryService.approve_and_send(
                session=db_session,
                email_id=email.id,
                idempotency_key=str(uuid.uuid4()),
                user_id=user.id,
            )
        assert exc_info.value.code == "SMTP_SEND_FAILED"

    # Verify rollback to pending_approval in database
    refreshed_email = await db_session.get(IncomingEmail, email.id)
    assert refreshed_email is not None
    assert refreshed_email.status == "pending_approval"


@pytest.mark.asyncio
async def test_ambiguous_failure_sets_delivery_unknown_and_operator_resolves(db_session: AsyncSession):
    """Timeout during DATA sets delivery_unknown; operator manually resolves via API."""
    now_utc = datetime.datetime.now(datetime.UTC)
    user = User(
        id=uuid.uuid4(),
        username="operator_ambig",
        normalized_username="operator_ambig",
        password_hash="hash",
        status="active",
    )
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US Ambig",
        brand_name="Wrydeco US",
        public_domain="wrydeco.com",
        status="active",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support_ambig@wrydeco.com",
        encrypted_password="enc_password",
    )
    db_session.add_all([user, store, mailbox])
    await db_session.flush()

    email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=503,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Important order issue",
        received_at=now_utc,
        status="pending_approval",
    )
    draft = ReplyDraft(
        id=uuid.uuid4(),
        incoming_email_id=email.id,
        store_profile_id=store.id,
        current_version_number=1,
        status="pending_approval",
    )
    db_session.add_all([email, draft])
    await db_session.flush()

    version = ReplyDraftVersion(
        id=uuid.uuid4(),
        draft_id=draft.id,
        incoming_email_id=email.id,
        version_number=1,
        subject="Re: Important order issue",
        body_text="Body",
        body_html="<p>Body</p>",
        language="en",
        source="ai",
        content_hash="abc",
        is_current_version=True,
    )
    db_session.add(version)
    await db_session.flush()
    draft.current_version_id = version.id
    await db_session.commit()

    # Drop during DATA command
    smtp_ambig = SMTPDeliveryResult(
        status="delivery_unknown",
        error_code="SMTP_DELIVERY_AMBIGUOUS",
        error_detail="Socket disconnected during DATA transmission",
    )

    with patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp:
        mock_smtp.return_value = smtp_ambig

        with pytest.raises(DeliveryBusinessError) as exc_info:
            await DeliveryService.approve_and_send(
                session=db_session,
                email_id=email.id,
                idempotency_key=str(uuid.uuid4()),
                user_id=user.id,
            )
        assert exc_info.value.code == "DELIVERY_UNKNOWN"

    # Status must be delivery_unknown in DB
    refreshed_email = await db_session.get(IncomingEmail, email.id)
    assert refreshed_email is not None
    assert refreshed_email.status == "delivery_unknown"

    # Operator checks webmail and confirms mail was indeed delivered -> resolves sent
    res_sent = await DeliveryService.resolve_delivery_unknown_sent(
        session=db_session,
        email_id=email.id,
        notes="Verified in webmail Sent folder at 10:15",
        user_id=user.id,
    )
    assert res_sent is True

    resolved_email = await db_session.get(IncomingEmail, email.id)
    assert resolved_email is not None
    assert resolved_email.status == "sent"
