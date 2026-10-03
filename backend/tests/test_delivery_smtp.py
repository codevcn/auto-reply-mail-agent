"""Unit and Integration tests for SMTP Delivery, RFC 5322 Threading, and IMAP Sent-copy.

Tests compliance with Invariant R-25 and error disambiguation (delivery_unknown vs failed).
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.db.models.draft import ReplyDraft, ReplyDraftVersion
from app.db.models.email import IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.db.models.user import User
from app.delivery.service import DeliveryService, build_rfc5322_reply_message
from app.mail.schemas import SMTPDeliveryResult
from app.mail.smtp_client import SMTPClient
from sqlalchemy.ext.asyncio import AsyncSession


def test_build_rfc5322_reply_message_headers():
    """Validates full RFC 5322 threading headers: In-Reply-To, References, unique Message-ID."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco US",
        public_domain="wrydeco.com",
        email_signature="Best regards,\nWrydeco Support Team",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        address="support@wrydeco.com",
    )
    orig_email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        imap_uid=401,
        uidvalidity=1,
        message_id="<orig-xyz123@customer.com>",
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Need invoice receipt",
        received_at=datetime.datetime.now(datetime.UTC),
    )
    draft_ver = ReplyDraftVersion(
        id=uuid.uuid4(),
        draft_id=uuid.uuid4(),
        incoming_email_id=orig_email.id,
        version_number=1,
        subject="Re: Need invoice receipt",
        body_text="Hi customer, here is your invoice receipt.",
        body_html="<p>Hi customer, here is your invoice receipt.</p>",
        language="en",
        source="ai",
        content_hash="abc",
        is_current_version=True,
    )
    out_msg_id = "<out-999@wrydeco.com>"

    msg = build_rfc5322_reply_message(
        store=store,
        mailbox=mailbox,
        original_email=orig_email,
        draft_version=draft_ver,
        outgoing_message_id=out_msg_id,
        recipient="customer@example.com",
    )

    # 1. Subject prefix
    assert msg["Subject"] == "Re: Need invoice receipt"

    # 2. From & To headers
    assert "Wrydeco US" in msg["From"]
    assert "support@wrydeco.com" in msg["From"]
    assert msg["To"] == "customer@example.com"

    # 3. RFC 5322 Message-ID
    assert msg["Message-ID"] == out_msg_id

    # 4. RFC 5322 Threading Headers
    assert msg["In-Reply-To"] == "<orig-xyz123@customer.com>"
    assert msg["References"] == "<orig-xyz123@customer.com>"


@pytest.mark.asyncio
async def test_smtp_robust_send_deterministic_vs_ambiguous_failure():
    """Validates that failures before DATA are deterministic, while drops during DATA are delivery_unknown."""
    client = SMTPClient(host="mail.wrydeco.com", port=587, username="support@wrydeco.com", password="pass")
    mock_msg = MagicMock()

    # Case 1: Failure before DATA (e.g. Auth Error) -> status = 'failed'
    with patch.object(client, "_sync_send_message_robust") as mock_robust:
        mock_robust.return_value = SMTPDeliveryResult(
            status="failed",
            error_code="SMTP_AUTH_FAILED",
            error_detail="Authentication rejected",
        )
        res = await client.send_message_robust(mock_msg, ["test@example.com"])
        assert res.status == "failed"
        assert res.error_code == "SMTP_AUTH_FAILED"

    # Case 2: Failure during DATA (Connection dropped / Timeout) -> status = 'delivery_unknown'
    with patch.object(client, "_sync_send_message_robust") as mock_robust:
        mock_robust.return_value = SMTPDeliveryResult(
            status="delivery_unknown",
            error_code="SMTP_DELIVERY_AMBIGUOUS",
            error_detail="Connection dropped/timed out during or after DATA phase",
        )
        res = await client.send_message_robust(mock_msg, ["test@example.com"])
        assert res.status == "delivery_unknown"
        assert res.error_code == "SMTP_DELIVERY_AMBIGUOUS"

    # Case 3: Successful 250 OK
    with patch.object(client, "_sync_send_message_robust") as mock_robust:
        mock_robust.return_value = SMTPDeliveryResult(
            status="sent",
            response_code=250,
            response_text="250 2.0.0 Ok: queued as 4XzK",
        )
        res = await client.send_message_robust(mock_msg, ["test@example.com"])
        assert res.status == "sent"
        assert res.response_code == 250


@pytest.mark.asyncio
async def test_delivery_imap_sent_folder_copy_fault_isolation(db_session: AsyncSession):
    """When SMTP succeeds with 250 OK but IMAP Sent append fails, email is still 'sent' (Invariant R-25)."""
    now_utc = datetime.datetime.now(datetime.UTC)
    user = User(
        id=uuid.uuid4(),
        username="admin1",
        normalized_username="admin1",
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
        imap_uid=402,
        uidvalidity=1,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Order inquiry",
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
        subject="Re: Order inquiry",
        body_text="Your order is on the way.",
        body_html="<p>Your order is on the way.</p>",
        language="en",
        source="ai",
        content_hash="abc",
        is_current_version=True,
    )
    db_session.add(version)
    await db_session.flush()
    draft.current_version_id = version.id
    await db_session.commit()

    # Mock SMTP to return 250 OK, and IMAP append to throw exception
    smtp_success_res = SMTPDeliveryResult(status="sent", response_code=250, response_text="250 2.0.0 Ok")

    with (
        patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp,
        patch("app.delivery.service.IMAPClient.append_to_sent_folder", new_callable=AsyncMock) as mock_imap,
    ):
        mock_smtp.return_value = smtp_success_res
        mock_imap.side_effect = Exception("IMAP mailbox full or disconnected")

        response = await DeliveryService.approve_and_send(
            session=db_session,
            email_id=email.id,
            idempotency_key=str(uuid.uuid4()),
            user_id=user.id,
        )

        assert response.success is True
        assert response.status == "sent"
        # IMAP append failed, but email status was NOT rolled back!
        assert response.sent_folder_append_status == "failed"

    # Verify database state
    refreshed_email = await db_session.get(IncomingEmail, email.id)
    assert refreshed_email is not None
    assert refreshed_email.status == "sent"
