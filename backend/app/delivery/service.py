"""Delivery Service: SMTP STARTTLS Dispatch, RFC 5322 Threading, Idempotency, and Sent-folder copy."""

from __future__ import annotations

import datetime
import email.utils
import uuid
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt_secret
from app.db.models.audit import AuditEvent
from app.db.models.draft import ReplyDeliveryAttempt, ReplyDraft, ReplyDraftVersion
from app.db.models.email import IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.delivery.schemas import ApproveAndSendResponse
from app.draft.prompt import format_plain_to_html
from app.mail.imap_client import IMAPClient
from app.mail.smtp_client import SMTPClient


class DeliveryBusinessError(Exception):
    """Exception for delivery errors with HTTP status codes."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def build_rfc5322_reply_message(
    store: StoreProfile,
    mailbox: Mailbox,
    original_email: IncomingEmail,
    draft_version: ReplyDraftVersion,
    outgoing_message_id: str,
    recipient: str,
) -> EmailMessage:
    """Builds RFC 5322 MIME message strictly preserving threading headers (Invariant R-25)."""
    msg = EmailMessage()

    # 1. Subject with Re: prefix normalization
    subj = draft_version.subject.strip()
    if not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"
    msg["Subject"] = subj

    # 2. From header
    brand_name = store.name.replace('"', "").strip() if store.name else "Support"
    msg["From"] = email.utils.formataddr((brand_name, mailbox.address))

    # 3. To header
    msg["To"] = recipient

    # 4. Message-ID (RFC 5322 Unique identifier)
    msg["Message-ID"] = outgoing_message_id

    # 5. Threading headers: In-Reply-To & References
    orig_msg_id = original_email.message_id
    if orig_msg_id:
        cleaned_orig = orig_msg_id.strip()
        if not (cleaned_orig.startswith("<") and cleaned_orig.endswith(">")):
            cleaned_orig = f"<{cleaned_orig}>"
        msg["In-Reply-To"] = cleaned_orig
        msg["References"] = cleaned_orig

    # 6. Body Content (Multipart Plain Text + Semantic HTML)
    signature = store.email_signature or f"Best regards,\n{brand_name} Support Team"
    body_text = f"{draft_version.body_text.strip()}\n\n--\n{signature}"
    msg.set_content(body_text)

    body_html = draft_version.body_html
    if not body_html:
        body_html = format_plain_to_html(draft_version.body_text)
    sig_html = f"<br><br>--<br>{signature.replace(chr(10), '<br>')}"
    full_html = f"{body_html}{sig_html}"
    msg.add_alternative(full_html, subtype="html")

    return msg


class DeliveryService:
    """Production delivery coordinator enforcing Zero Autonomous Sending and Concurrency Locks."""

    @staticmethod
    async def approve_and_send(
        session: AsyncSession,
        email_id: uuid.UUID,
        idempotency_key: str,
        user_id: uuid.UUID,
        draft_version_id: uuid.UUID | None = None,
        override_recipient: str | None = None,
    ) -> ApproveAndSendResponse:
        """Approves and dispatches email via SMTP STARTTLS, then copies to IMAP Sent folder.

        Enforces:
        - Invariant R-01: Explicit human approval.
        - Invariant R-26: Send Idempotency key & Concurrency protection.
        - Invariant R-25: RFC 5322 Threading headers & Sent-folder copy.
        """
        now_utc = datetime.datetime.now(datetime.UTC)

        # 1. Check Idempotency Key first (Invariant R-26)
        idemp_stmt = select(ReplyDeliveryAttempt).where(
            ReplyDeliveryAttempt.idempotency_key == idempotency_key
        )
        existing_attempt = (await session.execute(idemp_stmt)).scalar_one_or_none()
        if existing_attempt:
            if existing_attempt.status == "sent":
                # Idempotent replay
                return ApproveAndSendResponse(
                    success=True,
                    status="sent",
                    email_id=existing_attempt.incoming_email_id,
                    delivery_attempt_id=existing_attempt.id,
                    outgoing_message_id=existing_attempt.outgoing_message_id or "",
                    smtp_response=existing_attempt.smtp_response_summary,
                    sent_folder_append_status=existing_attempt.sent_folder_append_status,
                    sent_at=existing_attempt.sent_at,
                )
            elif existing_attempt.status == "sending":
                raise DeliveryBusinessError(
                    "SEND_IN_PROGRESS",
                    "A send request is already currently in progress for this idempotency key.",
                    409,
                )
            elif existing_attempt.status == "delivery_unknown":
                raise DeliveryBusinessError(
                    "DELIVERY_UNKNOWN_MANUAL_REQUIRED",
                    "Email state is ambiguous (delivery_unknown). Manual operator inspection required.",
                    409,
                )

        # 2. Pessimistic Row-Level Lock on IncomingEmail
        email_stmt = (
            select(IncomingEmail)
            .options(
                selectinload(IncomingEmail.mailbox),
                selectinload(IncomingEmail.store_profile),
                selectinload(IncomingEmail.reply_draft_rel).selectinload(ReplyDraft.versions),
            )
            .where(IncomingEmail.id == email_id)
            .with_for_update()
        )
        email = (await session.execute(email_stmt)).scalar_one_or_none()
        if not email:
            raise DeliveryBusinessError("EMAIL_NOT_FOUND", "Incoming email not found", 404)

        # Validate email status
        if email.status == "sent":
            raise DeliveryBusinessError(
                "EMAIL_ALREADY_SENT", "This email has already been replied to and sent.", 409
            )
        if email.status == "sending":
            raise DeliveryBusinessError(
                "SEND_IN_PROGRESS", "This email is currently being dispatched.", 409
            )
        if email.status == "delivery_unknown":
            raise DeliveryBusinessError(
                "DELIVERY_UNKNOWN_MANUAL_REQUIRED",
                "This email is in delivery_unknown state and requires manual verification.",
                409,
            )
        if email.status not in ("pending_approval", "drafted"):
            raise DeliveryBusinessError(
                "INVALID_STATE_FOR_SENDING",
                f"Email status '{email.status}' is not eligible for sending. Must be pending_approval.",
                400,
            )

        draft = email.reply_draft_rel
        if not draft or not draft.versions:
            raise DeliveryBusinessError("DRAFT_NOT_FOUND", "No reply draft available to send.", 404)

        # Resolve version to send
        target_version: ReplyDraftVersion | None = None
        if draft_version_id:
            target_version = next((v for v in draft.versions if v.id == draft_version_id), None)
            if not target_version:
                raise DeliveryBusinessError("VERSION_NOT_FOUND", "Specified draft version not found.", 404)
        else:
            target_version = next((v for v in draft.versions if v.id == draft.current_version_id), None)
            if not target_version:
                target_version = sorted(draft.versions, key=lambda v: v.version_number)[-1]

        mailbox = email.mailbox
        store = email.store_profile
        if not mailbox:
            raise DeliveryBusinessError("MAILBOX_NOT_CONFIGURED", "No mailbox configured for this email.", 500)

        # Determine recipient
        recipient = override_recipient or email.reply_to_email or email.sender_email
        if not recipient:
            recipient = email.sender_email

        # Generate unique Outgoing Message-ID
        mailbox_domain = mailbox.address.split("@")[-1] if "@" in mailbox.address else "mail.wrydeco.com"
        outgoing_msg_id = f"<{uuid.uuid4()}@{mailbox_domain}>"

        # 3. Transaction 1: Mark email & draft 'sending' and record attempt
        email.status = "sending"
        email.last_transition_at = now_utc
        email.row_version += 1

        draft.status = "sending"
        draft.updated_at = now_utc
        draft.row_version += 1

        attempt = ReplyDeliveryAttempt(
            id=uuid.uuid4(),
            incoming_email_id=email.id,
            draft_id=draft.id,
            draft_version_id=target_version.id,
            idempotency_key=idempotency_key,
            approved_by=user_id,
            approved_at=now_utc,
            status="sending",
            outgoing_message_id=outgoing_msg_id,
            smtp_started_at=now_utc,
            sent_folder_append_status="pending",
        )
        session.add(attempt)
        await session.commit()

        # 4. Out-of-transaction SMTP Dispatch (Network operation outside DB lock)
        raw_password = decrypt_secret(mailbox.encrypted_password)
        smtp_client = SMTPClient(
            host=mailbox.smtp_host,
            port=mailbox.smtp_port,
            username=mailbox.address,
            password=raw_password,
            tls_mode=mailbox.smtp_tls_mode,
        )

        mime_msg = build_rfc5322_reply_message(
            store=store,
            mailbox=mailbox,
            original_email=email,
            draft_version=target_version,
            outgoing_message_id=outgoing_msg_id,
            recipient=recipient,
        )

        delivery_result = await smtp_client.send_message_robust(mime_msg, [recipient])

        # 5. Transaction 2: Update outcomes based on delivery result
        completed_at = datetime.datetime.now(datetime.UTC)

        # Re-fetch email, draft, and attempt
        re_email = (
            await session.execute(select(IncomingEmail).where(IncomingEmail.id == email_id).with_for_update())
        ).scalar_one()
        re_draft = (
            await session.execute(select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id).with_for_update())
        ).scalar_one()
        re_attempt = (
            await session.execute(
                select(ReplyDeliveryAttempt).where(ReplyDeliveryAttempt.id == attempt.id).with_for_update()
            )
        ).scalar_one()

        re_attempt.smtp_completed_at = completed_at

        if delivery_result.status == "sent":
            re_email.status = "sent"
            re_email.last_transition_at = completed_at
            re_draft.status = "sent"
            re_draft.updated_at = completed_at

            re_attempt.status = "sent"
            re_attempt.sent_at = completed_at
            re_attempt.smtp_response_summary = delivery_result.response_text or "250 OK"

            session.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    event_type="EMAIL_SENT_SUCCESS",
                    actor_user_id=user_id,
                    target_type="incoming_email",
                    target_id=str(email_id),
                    store_profile_id=store.id,
                    safe_change_summary={
                        "outgoing_message_id": outgoing_msg_id,
                        "recipient": recipient,
                        "draft_version": target_version.version_number,
                    },
                )
            )

        elif delivery_result.status == "delivery_unknown":
            # Ambiguous drop/timeout during DATA command (Invariant R-25)
            re_email.status = "delivery_unknown"
            re_email.last_transition_at = completed_at
            re_draft.status = "delivery_unknown"
            re_draft.updated_at = completed_at

            re_attempt.status = "delivery_unknown"
            re_attempt.error_code = delivery_result.error_code or "SMTP_DELIVERY_AMBIGUOUS"
            re_attempt.error_detail = delivery_result.error_detail
            re_attempt.smtp_response_summary = delivery_result.error_detail

            session.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    event_type="EMAIL_DELIVERY_UNKNOWN",
                    actor_user_id=user_id,
                    target_type="incoming_email",
                    target_id=str(email_id),
                    store_profile_id=store.id,
                    safe_change_summary={
                        "outgoing_message_id": outgoing_msg_id,
                        "error": delivery_result.error_detail,
                    },
                )
            )

        else:
            # Deterministic failure before DATA phase: safe to rollback to pending_approval
            re_email.status = "pending_approval"
            re_email.last_transition_at = completed_at
            re_draft.status = "pending_approval"
            re_draft.updated_at = completed_at

            re_attempt.status = "failed"
            re_attempt.error_code = delivery_result.error_code or "SMTP_SEND_FAILED"
            re_attempt.error_detail = delivery_result.error_detail
            re_attempt.smtp_response_summary = delivery_result.error_detail

            session.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    event_type="EMAIL_SEND_FAILED",
                    actor_user_id=user_id,
                    target_type="incoming_email",
                    target_id=str(email_id),
                    store_profile_id=store.id,
                    safe_change_summary={
                        "error": delivery_result.error_detail,
                    },
                )
            )

        await session.commit()

        # 6. IMAP Sent-folder copy (Only when SMTP 250 OK)
        sent_append_status = "pending"
        if delivery_result.status == "sent":
            try:
                imap_client = IMAPClient(
                    host=mailbox.imap_host,
                    port=mailbox.imap_port,
                    username=mailbox.address,
                    password=raw_password,
                    tls_mode=mailbox.imap_tls_mode,
                )
                append_ok = await imap_client.append_to_sent_folder(mime_msg.as_bytes())
                sent_append_status = "success" if append_ok else "failed"
            except Exception:
                sent_append_status = "failed"

            # Update append status in DB
            att_update = (
                await session.execute(
                    select(ReplyDeliveryAttempt).where(ReplyDeliveryAttempt.id == attempt.id).with_for_update()
                )
            ).scalar_one_or_none()
            if att_update:
                att_update.sent_folder_append_status = sent_append_status
                await session.commit()

        if delivery_result.status == "delivery_unknown":
            raise DeliveryBusinessError(
                "DELIVERY_UNKNOWN",
                f"Connection dropped during SMTP transmission. Mail status is ambiguous: {delivery_result.error_detail}",
                502,
            )
        elif delivery_result.status == "failed":
            raise DeliveryBusinessError(
                "SMTP_SEND_FAILED",
                f"Failed to send email via SMTP: {delivery_result.error_detail}",
                502,
            )

        return ApproveAndSendResponse(
            success=True,
            status="sent",
            email_id=email_id,
            delivery_attempt_id=attempt.id,
            outgoing_message_id=outgoing_msg_id,
            smtp_response=delivery_result.response_text,
            sent_folder_append_status=sent_append_status,
            sent_at=completed_at,
        )

    @staticmethod
    async def resolve_delivery_unknown_sent(
        session: AsyncSession,
        email_id: uuid.UUID,
        notes: str,
        user_id: uuid.UUID,
    ) -> bool:
        """Manually marks a delivery_unknown email as 'sent' after operator webmail verification."""
        now_utc = datetime.datetime.now(datetime.UTC)
        email = (
            await session.execute(
                select(IncomingEmail).where(IncomingEmail.id == email_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not email or email.status != "delivery_unknown":
            raise DeliveryBusinessError(
                "INVALID_STATUS", "Email is not in delivery_unknown state", 400
            )

        email.status = "sent"
        email.last_transition_at = now_utc

        draft = (
            await session.execute(
                select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id).with_for_update()
            )
        ).scalar_one_or_none()
        if draft:
            draft.status = "sent"
            draft.updated_at = now_utc

        attempts = (
            await session.execute(
                select(ReplyDeliveryAttempt)
                .where(ReplyDeliveryAttempt.incoming_email_id == email_id)
                .order_by(ReplyDeliveryAttempt.created_at.desc())
            )
        ).scalars().all()
        if attempts:
            latest = attempts[0]
            latest.status = "sent"
            latest.sent_at = now_utc
            latest.error_detail = f"Manually verified sent by operator: {notes}"

        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                event_type="DELIVERY_UNKNOWN_RESOLVED_SENT",
                actor_user_id=user_id,
                target_type="incoming_email",
                target_id=str(email_id),
                store_profile_id=email.store_profile_id,
                safe_change_summary={"resolution": "marked_sent", "notes": notes},
            )
        )
        await session.commit()
        return True

    @staticmethod
    async def resolve_delivery_unknown_reopen(
        session: AsyncSession,
        email_id: uuid.UUID,
        notes: str,
        user_id: uuid.UUID,
    ) -> bool:
        """Manually resets a delivery_unknown email back to 'pending_approval' after verifying it was not delivered."""
        now_utc = datetime.datetime.now(datetime.UTC)
        email = (
            await session.execute(
                select(IncomingEmail).where(IncomingEmail.id == email_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not email or email.status != "delivery_unknown":
            raise DeliveryBusinessError(
                "INVALID_STATUS", "Email is not in delivery_unknown state", 400
            )

        email.status = "pending_approval"
        email.last_transition_at = now_utc

        draft = (
            await session.execute(
                select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id).with_for_update()
            )
        ).scalar_one_or_none()
        if draft:
            draft.status = "pending_approval"
            draft.updated_at = now_utc

        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                event_type="DELIVERY_UNKNOWN_RESOLVED_REOPEN",
                actor_user_id=user_id,
                target_type="incoming_email",
                target_id=str(email_id),
                store_profile_id=email.store_profile_id,
                safe_change_summary={"resolution": "reopened_pending_approval", "notes": notes},
            )
        )
        await session.commit()
        return True
