"""Mail Ingestion Service: Ingests metadata and fetches on-demand content from mailservers."""

from __future__ import annotations

import datetime
import email
import html
import re
import uuid
from email.policy import default

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt_secret
from app.db.models.email import EmailJob, IncomingEmail, MailboxCheckpoint
from app.db.models.store import Mailbox, StoreProfile
from app.ingestion.schemas import EmailContentResponse, IngestionResult
from app.mail.exceptions import (
    PiezaprintExclusionError,
    SourceMessageUnavailableError,
)
from app.mail.imap_client import IMAPClient
from app.shopify.service import ShopifyService

EXCLUDED_DOMAINS = ["piezaprint.com", "piezaprint"]


def _sanitize_html_content(raw_html: str) -> str:
    """Basic HTML sanitization removing dangerous script and iframe elements."""
    cleaned = re.sub(r"(?i)<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL)
    cleaned = re.sub(r"(?i)<iframe[^>]*>.*?</iframe>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?i)onload|onerror|onclick|javascript:", "", cleaned)
    return cleaned.strip()


def _decode_payload(payload: object, charset: str | None) -> str:
    """Safely decodes an email part payload into a string."""
    if isinstance(payload, bytes):
        return payload.decode(charset or "utf-8", errors="replace")
    return str(payload) if payload else ""


class MailIngestionService:
    """Orchestrates metadata ingestion adhering to R-04 (Zero Raw Body) and R-06 (PEEK preservation)."""

    @staticmethod
    async def get_or_create_checkpoint(
        db: AsyncSession,
        mailbox: Mailbox,
        folder: str = "INBOX",
        initial_uidvalidity: int | None = None,
        initial_baseline_uid: int | None = None,
    ) -> MailboxCheckpoint:
        """Finds existing MailboxCheckpoint or initializes a new one from store activation state."""
        stmt = select(MailboxCheckpoint).where(
            MailboxCheckpoint.mailbox_id == mailbox.id,
            MailboxCheckpoint.folder == folder,
        )
        res = await db.execute(stmt)
        cp = res.scalar_one_or_none()
        if cp:
            return cp

        # Fetch store profile for baseline
        stmt_store = select(StoreProfile).where(StoreProfile.id == mailbox.store_profile_id)
        res_store = await db.execute(stmt_store)
        store = res_store.scalar_one_or_none()

        baseline_uid = (
            initial_baseline_uid
            if initial_baseline_uid is not None
            else (store.activation_baseline_uid if store and store.activation_baseline_uid else 0)
        )
        uid_validity = (
            initial_uidvalidity
            if initial_uidvalidity is not None
            else (store.uid_validity if store and store.uid_validity else 1)
        )

        cp = MailboxCheckpoint(
            id=uuid.uuid4(),
            mailbox_id=mailbox.id,
            folder=folder,
            uid_validity=uid_validity,
            activation_baseline_uid=baseline_uid,
            last_durably_enqueued_uid=baseline_uid,
            state="active",
        )
        db.add(cp)
        await db.commit()
        await db.refresh(cp)
        return cp

    @staticmethod
    async def ingest_mailbox_messages(
        db: AsyncSession,
        mailbox_id: uuid.UUID,
        folder: str = "INBOX",
        candidate_uids: list[int] | None = None,
    ) -> IngestionResult:
        """Discovers and ingests new emails strictly above baseline UID."""
        stmt = (
            select(Mailbox)
            .options(selectinload(Mailbox.store_profile))
            .where(Mailbox.id == mailbox_id)
        )
        res = await db.execute(stmt)
        mailbox = res.scalar_one_or_none()
        if not mailbox:
            return IngestionResult(
                mailbox_id=mailbox_id, folder=folder, status="error", error_detail="MAILBOX_NOT_FOUND"
            )

        # Enforce R-03
        if any(exc in mailbox.address.lower() for exc in EXCLUDED_DOMAINS):
            raise PiezaprintExclusionError()

        raw_password = decrypt_secret(mailbox.encrypted_password)
        imap_client = IMAPClient(
            host=mailbox.imap_host,
            port=mailbox.imap_port,
            username=mailbox.address,
            password=raw_password,
            tls_mode=mailbox.imap_tls_mode,
        )

        checkpoint = await MailIngestionService.get_or_create_checkpoint(db, mailbox, folder)

        # Check server status and UIDVALIDITY
        test_res = await imap_client.test_connection()
        if not test_res.success:
            return IngestionResult(
                mailbox_id=mailbox.id,
                folder=folder,
                status="error",
                error_detail=test_res.error or "IMAP_CONNECTION_FAILED",
            )

        server_uidvalidity = test_res.uid_validity or checkpoint.uid_validity
        if checkpoint.uid_validity and server_uidvalidity != checkpoint.uid_validity:
            # SAFETY INVARIANT: UIDVALIDITY changed -> Pause ingestion
            checkpoint.state = "uidvalidity_changed"
            checkpoint.last_error_code = "IMAP_UIDVALIDITY_CHANGED"
            checkpoint.row_version += 1
            await db.commit()
            return IngestionResult(
                mailbox_id=mailbox.id,
                folder=folder,
                status="uidvalidity_changed",
                uid_validity=server_uidvalidity,
                error_detail="IMAP UIDVALIDITY changed on server. Ingestion safely paused.",
            )

        min_uid = max(checkpoint.activation_baseline_uid, checkpoint.last_durably_enqueued_uid)

        # Discover candidate UIDs if not directly provided by caller
        if candidate_uids is None:
            discovered_uids = await imap_client.search_new_uids(min_uid, folder)
        else:
            discovered_uids = [u for u in candidate_uids if u > checkpoint.activation_baseline_uid]

        discovered_uids = sorted(discovered_uids)
        if not discovered_uids:
            checkpoint.last_reconciled_at = datetime.datetime.now(datetime.UTC)
            checkpoint.row_version += 1
            await db.commit()
            return IngestionResult(
                mailbox_id=mailbox.id,
                folder=folder,
                new_emails_count=0,
                enqueued_jobs_count=0,
                skipped_count=0,
                highest_uid=min_uid,
                uid_validity=checkpoint.uid_validity,
                status="success",
            )

        new_emails_count = 0
        enqueued_jobs_count = 0
        skipped_count = 0
        highest_uid = checkpoint.last_durably_enqueued_uid

        for uid in discovered_uids:
            # Check deduplication in database first
            check_stmt = select(IncomingEmail).where(
                IncomingEmail.mailbox_id == mailbox.id,
                IncomingEmail.folder == folder,
                IncomingEmail.uidvalidity == checkpoint.uid_validity,
                IncomingEmail.imap_uid == uid,
            )
            existing_email = (await db.execute(check_stmt)).scalar_one_or_none()
            if existing_email:
                skipped_count += 1
                if uid > highest_uid:
                    highest_uid = uid
                continue

            # Fetch metadata non-destructively using BODY.PEEK
            header_meta = await imap_client.fetch_header_metadata(uid, folder)
            if not header_meta:
                continue

            # Check message-id deduplication if present
            if header_meta.message_id:
                msg_check_stmt = select(IncomingEmail).where(
                    IncomingEmail.mailbox_id == mailbox.id,
                    IncomingEmail.message_id == header_meta.message_id,
                )
                existing_msg = (await db.execute(msg_check_stmt)).scalar_one_or_none()
                if existing_msg:
                    skipped_count += 1
                    if uid > highest_uid:
                        highest_uid = uid
                    continue

            # Determine initial status (auto-submitted loop suppression)
            initial_status = "pending"
            manual_reason = None
            if header_meta.is_auto_submitted:
                initial_status = "manual_review"
                manual_reason = "AUTO_SUBMITTED_OR_BOUNCE"

            now_utc = datetime.datetime.now(datetime.UTC)
            new_email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=mailbox.store_profile_id,
                mailbox_id=mailbox.id,
                folder=folder,
                imap_uid=uid,
                uidvalidity=checkpoint.uid_validity,
                message_id=header_meta.message_id,
                sender_email=header_meta.from_address,
                sender_name=header_meta.sender_name,
                recipient_email=mailbox.address,
                reply_to_email=header_meta.reply_to,
                to_addresses=header_meta.to_addresses,
                cc_addresses=header_meta.cc_addresses,
                subject=header_meta.subject,
                received_at=header_meta.date or now_utc,
                status=initial_status,
                has_attachments=header_meta.has_attachments,
                attachment_count=header_meta.attachment_count,
                manual_review_reason=manual_reason,
                first_detected_at=now_utc,
                last_transition_at=now_utc,
            )
            db.add(new_email)
            await db.flush()

            # Enqueue Job into Transactional Queue
            new_job = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=new_email.id,
                store_profile_id=mailbox.store_profile_id,
                job_type="classify",
                status="queued",
                attempts=0,
                max_attempts=3,
                scheduled_at=now_utc,
            )
            db.add(new_job)

            new_emails_count += 1
            enqueued_jobs_count += 1
            if uid > highest_uid:
                highest_uid = uid

        checkpoint.last_durably_enqueued_uid = max(
            checkpoint.last_durably_enqueued_uid, highest_uid
        )
        checkpoint.last_reconciled_at = datetime.datetime.now(datetime.UTC)
        checkpoint.row_version += 1
        await db.commit()

        return IngestionResult(
            mailbox_id=mailbox.id,
            folder=folder,
            new_emails_count=new_emails_count,
            enqueued_jobs_count=enqueued_jobs_count,
            skipped_count=skipped_count,
            highest_uid=highest_uid,
            uid_validity=checkpoint.uid_validity,
            status="success",
        )


class MailFetchService:
    """Fetches full email body and attachments on-demand from IMAP mailserver (R-04)."""

    @staticmethod
    async def fetch_email_content(
        db: AsyncSession,
        email_id: uuid.UUID,
    ) -> EmailContentResponse:
        """Reads raw MIME from mailserver on-demand, sanitizes HTML, and never persists body to DB."""
        stmt = (
            select(IncomingEmail)
            .options(
                selectinload(IncomingEmail.mailbox),
                selectinload(IncomingEmail.order_snapshots),
                selectinload(IncomingEmail.product_snapshots),
            )
            .where(IncomingEmail.id == email_id)
        )
        res = await db.execute(stmt)
        incoming = res.scalar_one_or_none()
        if not incoming:
            raise ValueError("EMAIL_NOT_FOUND")

        mailbox: Mailbox = incoming.mailbox
        raw_password = decrypt_secret(mailbox.encrypted_password)

        imap_client = IMAPClient(
            host=mailbox.imap_host,
            port=mailbox.imap_port,
            username=mailbox.address,
            password=raw_password,
            tls_mode=mailbox.imap_tls_mode,
        )

        raw_bytes = await imap_client.fetch_raw_email_peek(incoming.imap_uid, incoming.folder)
        if not raw_bytes:
            # Source message missing/deleted on mailserver
            incoming.status = "manual_review"
            incoming.manual_review_reason = "SOURCE_MESSAGE_UNAVAILABLE"
            incoming.row_version += 1
            await db.commit()
            raise SourceMessageUnavailableError(incoming.imap_uid)

        # Parse message
        msg = email.message_from_bytes(raw_bytes, policy=default)
        body_text = ""
        body_html = ""
        attachments_meta = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    filename = part.get_filename() or "untitled"
                    payload = part.get_payload(decode=True) or b""
                    attachments_meta.append(
                        {
                            "filename": filename,
                            "content_type": content_type,
                            "size_bytes": len(payload),
                        }
                    )
                elif content_type == "text/plain" and not body_text:
                    body_text = _decode_payload(part.get_payload(decode=True), part.get_content_charset())
                elif content_type == "text/html" and not body_html:
                    body_html = _decode_payload(part.get_payload(decode=True), part.get_content_charset())
        else:
            content_type = msg.get_content_type()
            decoded = _decode_payload(msg.get_payload(decode=True), msg.get_content_charset())
            if content_type == "text/html":
                body_html = decoded
            else:
                body_text = decoded

        sanitized_html = _sanitize_html_content(body_html) if body_html else html.escape(body_text)

        # Build order snapshot dict if present
        order_snap_dict = None
        if incoming.order_snapshots:
            latest_snap = incoming.order_snapshots[-1]
            order_snap_dict = {
                "lookup_status": latest_snap.lookup_status,
                "matched_order_count": latest_snap.matched_order_count,
                "has_paid_order": latest_snap.has_paid_order,
                "has_active_order": latest_snap.has_active_order,
                "has_cancelled_order": latest_snap.has_cancelled_order,
                "has_refunded_order": latest_snap.has_refunded_order,
                "has_fulfilled_order": latest_snap.has_fulfilled_order,
                "latest_order_name": latest_snap.latest_order_name,
                "latest_order_total_price": latest_snap.latest_order_total_price,
                "latest_order_currency": latest_snap.latest_order_currency,
                "latest_order_financial_status": latest_snap.latest_order_financial_status,
                "latest_order_fulfillment_status": latest_snap.latest_order_fulfillment_status,
                "line_items_summary": latest_snap.line_items_summary,
                "lookup_checked_at": latest_snap.lookup_checked_at.isoformat(),
            }

        # Build product snapshot dict if present
        prod_snap_dict = None
        if incoming.product_snapshots:
            latest_prod = incoming.product_snapshots[-1]
            prod_snap_dict = {
                "search_query": latest_prod.search_query,
                "matched_count": latest_prod.matched_count,
                "product_resolved": latest_prod.product_resolved,
                "matched_products": latest_prod.matched_products,
                "warning_codes": latest_prod.warning_codes,
            }

        # Check Stale Draft status against current store policies (Invariant R-18)
        is_stale = incoming.is_stale
        stale_reason = incoming.stale_reason
        stale_details = incoming.stale_details or []

        if incoming.policy_hashes_used and incoming.store_profile_id:
            curr_policies = await ShopifyService.get_effective_store_policies(
                session=db,
                store_profile_id=incoming.store_profile_id,
            )
            curr_hashes = {p_type: p.content_hash for p_type, p in curr_policies.items()}
            freshness = ShopifyService.check_draft_policy_freshness(
                policy_hashes_used=incoming.policy_hashes_used,
                current_policy_hashes=curr_hashes,
            )
            if freshness.is_stale:
                is_stale = True
                stale_reason = freshness.stale_reason
                stale_details = freshness.stale_details
                if not incoming.is_stale:
                    incoming.is_stale = True
                    incoming.stale_reason = freshness.stale_reason
                    incoming.stale_details = freshness.stale_details
                    await db.commit()

        return EmailContentResponse(
            email_id=incoming.id,
            mailbox_address=mailbox.address,
            imap_uid=incoming.imap_uid,
            subject=incoming.subject or "(No Subject)",
            sender_email=incoming.sender_email,
            sender_name=incoming.sender_name,
            recipient_email=incoming.recipient_email,
            received_at=incoming.received_at,
            body_text=body_text.strip(),
            body_html_sanitized=sanitized_html,
            has_attachments=len(attachments_meta) > 0,
            attachment_count=len(attachments_meta),
            attachments_metadata=attachments_meta,
            order_snapshot=order_snap_dict,
            product_snapshot=prod_snap_dict,
            is_stale=is_stale,
            stale_reason=stale_reason,
            stale_details=stale_details,
            policy_hashes_used=incoming.policy_hashes_used,
            customer_status=incoming.customer_status,
            review_reason_code=incoming.review_reason_code,
            manual_review_reason=incoming.manual_review_reason,
            intent=incoming.classification_category,
            spam_status=incoming.spam_status,
        )
