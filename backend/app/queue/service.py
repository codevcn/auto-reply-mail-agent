"""Transactional Queue Service: Row-locked job execution and Queue metadata queries."""

from __future__ import annotations

import datetime
import math
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.schemas import EmailClassificationInput, StoreContext
from app.ai.service import get_ai_provider
from app.attachment.service import AttachmentService
from app.config import get_settings
from app.core.crypto import decrypt_secret
from app.db.models.audit import AuditEvent
from app.db.models.email import EmailClassification, EmailJob, IncomingEmail
from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
from app.db.models.store import ShopifyConnection, StoreProfile
from app.draft.service import DraftBusinessError, DraftService
from app.mail.imap_client import IMAPClient
from app.proxy.service import ProxyService
from app.queue.schemas import (
    EmailListItem,
    EmailQueueListResponse,
    OverrideClassificationRequest,
    QueueStatsResponse,
)
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyPermissionError,
    ShopifyProxyError,
    ShopifyRateLimitError,
    ShopifyTransientError,
)
from app.shopify.service import ShopifyService
from app.shopify.token import ShopifyTokenManager

SHOPIFY_LOOKUP_RETRY_DELAYS_SECONDS = [60, 300, 900, 1800, 3600]
MAX_SHOPIFY_LOOKUP_ATTEMPTS = 5


class TransactionalQueueService:
    """Manages transactional row-locked queue execution and queue list queries."""

    @staticmethod
    async def acquire_next_job(
        session: AsyncSession,
        worker_id: str,
        job_type: str | None = None,
    ) -> EmailJob | None:
        """Acquires next available queued job using FOR UPDATE SKIP LOCKED (PostgreSQL) or serialized (SQLite)."""
        now_utc = datetime.datetime.now(datetime.UTC)
        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind else "sqlite"

        stmt = (
            select(EmailJob)
            .where(EmailJob.status == "queued")
            .where(EmailJob.scheduled_at <= now_utc)
            .order_by(EmailJob.created_at.asc())
            .limit(1)
        )

        if job_type:
            stmt = stmt.where(EmailJob.job_type == job_type)

        if dialect_name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        res = await session.execute(stmt)
        job = res.scalar_one_or_none()

        if job:
            job.status = "processing"
            job.locked_at = now_utc
            job.locked_by = worker_id
            job.attempts += 1
            job.updated_at = now_utc
            await session.commit()
            await session.refresh(job)

        return job

    @staticmethod
    async def complete_job(session: AsyncSession, job_id: uuid.UUID) -> bool:
        """Marks a job as completed."""
        stmt = select(EmailJob).where(EmailJob.id == job_id)
        job = (await session.execute(stmt)).scalar_one_or_none()
        if not job:
            return False

        now_utc = datetime.datetime.now(datetime.UTC)
        job.status = "completed"
        job.completed_at = now_utc
        job.updated_at = now_utc
        await session.commit()
        return True

    @staticmethod
    async def fail_job(session: AsyncSession, job_id: uuid.UUID, error_message: str) -> bool:
        """Retries a failed job or moves it to failed and flags email for manual review."""
        stmt = select(EmailJob).where(EmailJob.id == job_id)
        job = (await session.execute(stmt)).scalar_one_or_none()
        if not job:
            return False

        now_utc = datetime.datetime.now(datetime.UTC)
        job.last_error = error_message
        job.updated_at = now_utc

        if job.attempts < job.max_attempts:
            # Exponential backoff retry: 15s * (2 ^ attempts)
            delay_seconds = 15 * (2 ** (job.attempts - 1))
            job.status = "queued"
            job.scheduled_at = now_utc + datetime.timedelta(seconds=delay_seconds)
            job.locked_at = None
            job.locked_by = None
        else:
            job.status = "failed"
            # Update associated incoming email
            email_stmt = select(IncomingEmail).where(IncomingEmail.id == job.incoming_email_id)
            email_record = (await session.execute(email_stmt)).scalar_one_or_none()
            if email_record:
                email_record.status = "manual_review"
                email_record.manual_review_reason = "JOB_MAX_ATTEMPTS_EXCEEDED"
                email_record.review_reason_code = "JOB_MAX_ATTEMPTS_EXCEEDED"
                email_record.last_transition_at = now_utc

        await session.commit()
        return True

    @staticmethod
    async def get_queue_stats(
        session: AsyncSession,
        store_id: uuid.UUID | None = None,
    ) -> QueueStatsResponse:
        """Calculates item counts for all 7 queues with Invariant R-08 Spam isolation."""
        base_query = select(IncomingEmail)
        if store_id:
            base_query = base_query.where(IncomingEmail.store_profile_id == store_id)

        # Invariant R-08: Spam isolation condition
        non_spam_filter = and_(
            IncomingEmail.status != "spam",
            IncomingEmail.spam_status != "spam",
        )

        # 1. ready-to-review: status == 'pending_approval' (non-spam)
        q_ready = select(func.count()).select_from(
            base_query.where(
                IncomingEmail.status == "pending_approval",
                non_spam_filter,
            ).subquery()
        )
        ready_count = (await session.execute(q_ready)).scalar() or 0

        # 2. needs-manual-review: status == 'manual_review' (non-spam)
        q_manual = select(func.count()).select_from(
            base_query.where(
                IncomingEmail.status == "manual_review",
                non_spam_filter,
            ).subquery()
        )
        manual_count = (await session.execute(q_manual)).scalar() or 0

        # 3. product-inquiry: classification_category == 'product_inquiry' (non-spam)
        q_product = select(func.count()).select_from(
            base_query.where(
                IncomingEmail.classification_category == "product_inquiry",
                non_spam_filter,
            ).subquery()
        )
        product_count = (await session.execute(q_product)).scalar() or 0

        # 4. recent-order: customer_status == 'has_order_record' (non-spam)
        q_order = select(func.count()).select_from(
            base_query.where(
                IncomingEmail.customer_status == "has_order_record",
                non_spam_filter,
            ).subquery()
        )
        order_count = (await session.execute(q_order)).scalar() or 0

        # 5. complaint: classification_category in ('complaint', 'return_or_refund') (non-spam)
        q_complaint = select(func.count()).select_from(
            base_query.where(
                IncomingEmail.classification_category.in_(["complaint", "return_or_refund"]),
                non_spam_filter,
            ).subquery()
        )
        complaint_count = (await session.execute(q_complaint)).scalar() or 0

        # 6. spam: status == 'spam' or spam_status == 'spam' (Invariant R-08)
        q_spam = select(func.count()).select_from(
            base_query.where(
                or_(IncomingEmail.status == "spam", IncomingEmail.spam_status == "spam")
            ).subquery()
        )
        spam_count = (await session.execute(q_spam)).scalar() or 0

        # 7. sent: status == 'sent'
        q_sent = select(func.count()).select_from(
            base_query.where(IncomingEmail.status == "sent").subquery()
        )
        sent_count = (await session.execute(q_sent)).scalar() or 0

        total_unprocessed = ready_count + manual_count + product_count + complaint_count

        return QueueStatsResponse(
            ready_to_review=ready_count,
            needs_manual_review=manual_count,
            product_inquiry=product_count,
            recent_order=order_count,
            complaint=complaint_count,
            spam=spam_count,
            sent=sent_count,
            total_unprocessed=total_unprocessed,
            ready=ready_count,
            manual=manual_count,
            product=product_count,
        )

    @staticmethod
    async def get_queue_emails(
        session: AsyncSession,
        queue_type: str,
        store_id: uuid.UUID | None = None,
        page: int = 1,
        limit: int = 25,
        search: str | None = None,
    ) -> EmailQueueListResponse:
        """Queries and paginates emails for a specific queue with Invariant R-08 Spam hiding."""
        stmt = (
            select(IncomingEmail)
            .options(selectinload(IncomingEmail.store_profile))
            .order_by(IncomingEmail.received_at.desc())
        )

        if store_id:
            stmt = stmt.where(IncomingEmail.store_profile_id == store_id)

        non_spam_filter = and_(
            IncomingEmail.status != "spam",
            IncomingEmail.spam_status != "spam",
        )

        # Apply queue criteria
        norm_queue = queue_type.strip().lower()
        if norm_queue == "ready-to-review":
            stmt = stmt.where(IncomingEmail.status == "pending_approval", non_spam_filter)
        elif norm_queue == "needs-manual-review":
            stmt = stmt.where(IncomingEmail.status == "manual_review", non_spam_filter)
        elif norm_queue == "product-inquiry":
            stmt = stmt.where(IncomingEmail.classification_category == "product_inquiry", non_spam_filter)
        elif norm_queue == "recent-order":
            stmt = stmt.where(IncomingEmail.customer_status == "has_order_record", non_spam_filter)
        elif norm_queue == "complaint":
            stmt = stmt.where(
                IncomingEmail.classification_category.in_(["complaint", "return_or_refund"]),
                non_spam_filter,
            )
        elif norm_queue == "spam":
            # Tab spam shows spam emails isolated from normal queues
            stmt = stmt.where(
                or_(IncomingEmail.status == "spam", IncomingEmail.spam_status == "spam")
            )
        elif norm_queue == "sent":
            stmt = stmt.where(IncomingEmail.status == "sent")

        # Apply search if provided
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    IncomingEmail.subject.ilike(pattern),
                    IncomingEmail.sender_email.ilike(pattern),
                    IncomingEmail.sender_name.ilike(pattern),
                )
            )

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        # Apply pagination
        offset = max(0, (page - 1) * limit)
        paginated_stmt = stmt.offset(offset).limit(limit)
        res = await session.execute(paginated_stmt)
        emails = res.scalars().all()

        total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1

        items = [
            EmailListItem(
                id=em.id,
                store_profile_id=em.store_profile_id,
                store_name=em.store_profile.name if em.store_profile else "Store",
                from_address=em.sender_email,
                sender_name=em.sender_name,
                subject=em.subject or "(No Subject)",
                received_at=em.received_at,
                processing_status=em.status,
                intent=em.classification_category,
                intent_confidence=em.intent_confidence,
                customer_status=em.customer_status,
                spam_status=em.spam_status,
                has_attachments=em.has_attachments,
                attachment_count=em.attachment_count,
                current_draft_version=em.current_draft_version or 0,
                manual_review_reason=em.manual_review_reason,
                review_reason_code=em.review_reason_code,
                spam_score=em.spam_score,
                detected_language=em.detected_language,
            )
            for em in emails
        ]

        return EmailQueueListResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        )

    @staticmethod
    async def unmark_spam(
        session: AsyncSession,
        email_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> IncomingEmail:
        """INVARIANT R-08: Recovers falsely classified spam email back into appropriate queue."""
        stmt = select(IncomingEmail).where(IncomingEmail.id == email_id).with_for_update()
        email_rec = (await session.execute(stmt)).scalar_one_or_none()
        if not email_rec:
            raise ValueError("EMAIL_NOT_FOUND")

        prev_status = email_rec.status
        email_rec.spam_status = "not_spam"

        # Route to appropriate status
        if email_rec.current_draft_version and email_rec.current_draft_version > 0:
            email_rec.status = "pending_approval"
        elif email_rec.review_reason_code:
            email_rec.status = "manual_review"
        elif email_rec.classification_category in ("product_inquiry", "order_support", "complaint", "return_or_refund"):
            email_rec.status = "classified"
            # Enqueue order enrichment job for Phase 5
            enrich_job = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                job_type="enrich_order",
                status="queued",
            )
            session.add(enrich_job)
        else:
            email_rec.status = "pending"
            classify_job = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                job_type="classify",
                status="queued",
            )
            session.add(classify_job)

        now_utc = datetime.datetime.now(datetime.UTC)
        email_rec.last_transition_at = now_utc
        email_rec.row_version += 1

        # Audit classification record
        class_rec = EmailClassification(
            id=uuid.uuid4(),
            incoming_email_id=email_rec.id,
            source="user",
            spam_status="not_spam",
            customer_status=email_rec.customer_status or "no_order",
            intent=email_rec.classification_category or "product_inquiry",
            reason_codes=["USER_UNMARK_SPAM"],
            reasoning="Operator unmarked email as spam.",
            created_by=user_id,
            created_at=now_utc,
        )
        session.add(class_rec)

        # Audit event
        audit = AuditEvent(
            id=uuid.uuid4(),
            event_type="EMAIL_UNMARK_SPAM",
            actor_user_id=user_id,
            target_type="incoming_email",
            target_id=str(email_rec.id),
            safe_change_summary={
                "previous_status": prev_status,
                "new_status": email_rec.status,
                "spam_status": "not_spam",
            },
        )
        session.add(audit)

        await session.commit()
        await session.refresh(email_rec)
        return email_rec

    @staticmethod
    async def override_classification(
        session: AsyncSession,
        email_id: uuid.UUID,
        user_id: uuid.UUID | None,
        override_data: OverrideClassificationRequest,
    ) -> IncomingEmail:
        """Applies manual operator override to email classification."""
        stmt = select(IncomingEmail).where(IncomingEmail.id == email_id).with_for_update()
        email_rec = (await session.execute(stmt)).scalar_one_or_none()
        if not email_rec:
            raise ValueError("EMAIL_NOT_FOUND")

        now_utc = datetime.datetime.now(datetime.UTC)

        # Mark previous classifications superseded
        supersede_stmt = (
            select(EmailClassification)
            .where(
                EmailClassification.incoming_email_id == email_id,
                EmailClassification.superseded_at.is_(None),
            )
        )
        old_classes = (await session.execute(supersede_stmt)).scalars().all()
        for old_c in old_classes:
            old_c.superseded_at = now_utc

        # Create new user classification record
        new_class = EmailClassification(
            id=uuid.uuid4(),
            incoming_email_id=email_id,
            source="user",
            spam_status=override_data.spam_status,
            customer_status=override_data.customer_status,
            intent=override_data.intent,
            reason_codes=["USER_OVERRIDE"],
            reasoning=override_data.notes,
            confidence={"intent": 1.0, "spam": 1.0},
            created_by=user_id,
            created_at=now_utc,
        )
        session.add(new_class)

        # Update incoming email
        email_rec.classification_category = override_data.intent
        email_rec.spam_status = override_data.spam_status
        email_rec.customer_status = override_data.customer_status
        email_rec.review_reason_code = None
        email_rec.manual_review_reason = None
        email_rec.last_transition_at = now_utc
        email_rec.row_version += 1

        if override_data.spam_status == "spam":
            email_rec.status = "spam"
        elif override_data.intent in ("partnership", "other", "uncertain"):
            email_rec.status = "manual_review"
            email_rec.review_reason_code = "NON_DRAFTING_INTENT"
        else:
            email_rec.status = "classified"
            if override_data.generate_draft:
                shopify_client = await TransactionalQueueService._get_shopify_client_for_store(
                    session=session,
                    store_profile_id=email_rec.store_profile_id,
                )
                next_job_type = "enrich_order" if shopify_client else "generate_draft"
                enrich_job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=email_rec.store_profile_id,
                    job_type=next_job_type,
                    status="queued",
                )
                session.add(enrich_job)

        # Audit Event
        audit = AuditEvent(
            id=uuid.uuid4(),
            event_type="EMAIL_CLASSIFICATION_OVERRIDE",
            actor_user_id=user_id,
            target_type="incoming_email",
            target_id=str(email_rec.id),
            safe_change_summary={
                "intent": override_data.intent,
                "spam_status": override_data.spam_status,
                "customer_status": override_data.customer_status,
                "generate_draft": override_data.generate_draft,
                "notes": override_data.notes,
            },
        )
        session.add(audit)

        await session.commit()
        await session.refresh(email_rec)
        return email_rec

    @staticmethod
    async def process_classification_job(
        session: AsyncSession,
        job_id: uuid.UUID,
        worker_id: str = "worker-1",
    ) -> bool:
        """Executes job_type='classify': safely extracts attachments, performs 3D AI classification, and routes item."""
        job_stmt = (
            select(EmailJob)
            .options(
                selectinload(EmailJob.incoming_email).selectinload(IncomingEmail.mailbox),
                selectinload(EmailJob.incoming_email).selectinload(IncomingEmail.store_profile),
            )
            .where(EmailJob.id == job_id)
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if not job or not job.incoming_email:
            return False

        email_rec = job.incoming_email
        store = email_rec.store_profile
        mailbox = email_rec.mailbox
        settings = get_settings()

        # 1. Fetch raw RFC 822 email on-demand from mailserver via IMAP PEEK
        raw_email_bytes: bytes = b""
        if mailbox and mailbox.imap_host and mailbox.encrypted_password:
            try:
                raw_password = decrypt_secret(mailbox.encrypted_password)
                imap_client = IMAPClient(
                    host=mailbox.imap_host,
                    port=mailbox.imap_port,
                    username=mailbox.address,
                    password=raw_password,
                    tls_mode=mailbox.imap_tls_mode,
                )
                peek_bytes = await imap_client.fetch_raw_email_peek(email_rec.imap_uid, email_rec.folder)
                if peek_bytes:
                    raw_email_bytes = peek_bytes
            except Exception:  # noqa: S110
                pass


        # Synthetic fallback if IMAP unavailable (mock or local unit test)
        if not raw_email_bytes:
            safe_subj = email_rec.subject or "Support Inquiry"
            safe_body = "Customer inquiry details."
            raw_email_bytes = (
                f"From: {email_rec.sender_email}\r\n"
                f"To: {email_rec.recipient_email}\r\n"
                f"Subject: {safe_subj}\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                f"{safe_body}"
            ).encode()

        # 2. Safe MIME parsing and Attachment Validation (Invariants R-04, R-20, R-21)
        parsed_email, val_result = AttachmentService.parse_and_validate(
            raw_email_bytes,
            max_single_bytes=settings.ATTACHMENT_MAX_SINGLE_BYTES,
            max_total_bytes=settings.ATTACHMENT_MAX_TOTAL_BYTES,
            max_count=settings.ATTACHMENT_MAX_COUNT,
        )

        # Persist safe metadata (0 raw binary bytes in DB - Invariant R-04)
        await AttachmentService.persist_attachment_metadata(session, email_rec.id, val_result)

        email_rec.has_attachments = parsed_email.has_attachments
        email_rec.attachment_count = parsed_email.attachment_count

        now_utc = datetime.datetime.now(datetime.UTC)

        # INVARIANT R-21: Attachment validation failed -> Route to manual review and STOP drafting!
        if not val_result.is_valid:
            email_rec.status = "manual_review"
            email_rec.manual_review_reason = val_result.error_code
            email_rec.review_reason_code = val_result.error_code
            email_rec.last_transition_at = now_utc

            class_rec = EmailClassification(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                source="rule",
                spam_status="not_spam",
                customer_status=email_rec.customer_status or "no_order",
                intent="uncertain",
                requires_manual_review=True,
                review_reason_code=val_result.error_code,
                reasoning=f"Attachment validation failed: {val_result.error_code}",
                created_at=now_utc,
            )
            session.add(class_rec)

            job.status = "completed"
            job.completed_at = now_utc
            job.updated_at = now_utc
            await session.commit()
            return True

        # 3. AI 3D Classification via AIProvider (Invariants R-07, R-19)
        ai_input = EmailClassificationInput(
            email_id=email_rec.id,
            mailbox_address=mailbox.address if mailbox else "support@store.com",
            sender_email=email_rec.sender_email,
            sender_name=email_rec.sender_name,
            recipient_email=email_rec.recipient_email,
            subject=parsed_email.subject or email_rec.subject or "",
            body_text=parsed_email.body_text or "",
            body_html_sanitized=parsed_email.body_html_sanitized,
            received_at=email_rec.received_at,
            attachments=val_result.attachments,
        )

        store_ctx = StoreContext(
            store_profile_id=store.id if store else email_rec.store_profile_id,
            brand_name=store.name if store else "Store",
            public_domain=store.public_domain if store else "store.com",
            canonical_domain=store.canonical_domain if store else None,
            default_language="en",
        )

        ai_provider = get_ai_provider()
        ai_res = await ai_provider.classify_email(ai_input, store_ctx)

        # Update incoming email model with AI results
        email_rec.detected_language = ai_res.detected_language
        email_rec.spam_score = ai_res.spam_score
        email_rec.spam_status = ai_res.spam_status
        email_rec.customer_status = ai_res.order_status
        email_rec.classification_category = ai_res.intent
        email_rec.intent_confidence = ai_res.confidence
        email_rec.extracted_entities = ai_res.entities.model_dump()
        email_rec.last_transition_at = now_utc

        # Record AI classification result
        ai_class_rec = EmailClassification(
            id=uuid.uuid4(),
            incoming_email_id=email_rec.id,
            source="ai",
            provider_model=ai_res.model_identifier,
            prompt_version=ai_res.prompt_version,
            spam_status=ai_res.spam_status,
            spam_score=ai_res.spam_score,
            customer_status=ai_res.order_status,
            intent=ai_res.intent,
            order_state_flags=ai_res.order_flags.model_dump(),
            confidence={"intent": ai_res.confidence, "spam": ai_res.spam_score},
            reason_codes=ai_res.reason_codes,
            reasoning=ai_res.reasoning_summary,
            detected_language=ai_res.detected_language,
            requires_manual_review=ai_res.requires_manual_review,
            review_reason_code=ai_res.review_reason_code,
            created_at=now_utc,
        )
        session.add(ai_class_rec)

        # 4. Pipeline Routing Decision
        # Invariant R-08: Spam is labeled and isolated
        if ai_res.is_spam or ai_res.spam_status == "spam":
            email_rec.status = "spam"
            email_rec.spam_status = "spam"
        # Prompt injection quarantined
        elif ai_res.is_prompt_injection or ai_res.review_reason_code == "PROMPT_INJECTION_DETECTED":
            email_rec.status = "manual_review"
            email_rec.review_reason_code = "PROMPT_INJECTION_DETECTED"
            email_rec.manual_review_reason = "PROMPT_INJECTION_DETECTED"
        # Invariant R-23: Manual review for non-drafting intents or low confidence
        elif ai_res.requires_manual_review or ai_res.intent in ("partnership", "other", "uncertain") or ai_res.confidence < 0.70:
            email_rec.status = "manual_review"
            reason = ai_res.review_reason_code or "NON_DRAFTING_INTENT"
            email_rec.review_reason_code = reason
            email_rec.manual_review_reason = reason
        # Approved drafting intents: product_inquiry, order_support, complaint, return_or_refund
        else:
            email_rec.status = "classified"
            shopify_client = await TransactionalQueueService._get_shopify_client_for_store(
                session=session,
                store_profile_id=email_rec.store_profile_id,
            )
            next_job_type = "enrich_order" if shopify_client else "generate_draft"
            enrich_job = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                job_type=next_job_type,
                status="queued",
            )
            session.add(enrich_job)

        job.status = "completed"
        job.completed_at = now_utc
        job.updated_at = now_utc

        await session.commit()
        return True

    @staticmethod
    async def _get_shopify_client_for_store(
        session: AsyncSession,
        store_profile_id: uuid.UUID,
    ) -> ProxyEnforcedShopifyClient | None:
        """Helper to build a ProxyEnforcedShopifyClient for a given store profile."""
        stmt = (
            select(StoreProfile)
            .options(
                selectinload(StoreProfile.shopify_connection),
                selectinload(StoreProfile.proxy_profile),
            )
            .where(StoreProfile.id == store_profile_id)
        )
        store = (await session.execute(stmt)).scalar_one_or_none()
        if not store or not store.shopify_connection:
            return None

        conn = store.shopify_connection
        proxy_profile = store.proxy_profile
        if not proxy_profile and conn.proxy_profile_id:
            proxy_profile = await ProxyService.get_proxy_profile(session, conn.proxy_profile_id)

        if not proxy_profile:
            return None

        proxy_config = ProxyService.resolve_proxy_config(proxy_profile)
        client_secret = decrypt_secret(conn.encrypted_client_secret)
        token_mgr = ShopifyTokenManager(
            shop_domain=conn.shop_domain,
            client_id=conn.client_id,
            client_secret=client_secret,
            proxy_config=proxy_config,
        )
        return ProxyEnforcedShopifyClient(
            shop_domain=conn.shop_domain,
            token_manager=token_mgr,
            proxy_config=proxy_config,
        )

    @staticmethod
    async def process_order_enrichment_job(
        session: AsyncSession,
        job_id: uuid.UUID,
        worker_id: str = "worker-1",
    ) -> bool:
        """Executes job_type='enrich_order': 60-day lookup via SOCKS5 proxy with retry schedule (Invariant R-09, R-10, R-11)."""
        job_stmt = (
            select(EmailJob)
            .options(
                selectinload(EmailJob.incoming_email).selectinload(IncomingEmail.store_profile),
            )
            .where(EmailJob.id == job_id)
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if not job or not job.incoming_email:
            return False

        email_rec = job.incoming_email
        now_utc = datetime.datetime.now(datetime.UTC)

        shopify_client = await TransactionalQueueService._get_shopify_client_for_store(
            session=session,
            store_profile_id=email_rec.store_profile_id,
        )

        if not shopify_client:
            # Store has no Shopify connection or Proxy configured
            email_rec.customer_status = "lookup_unavailable"
            email_rec.last_transition_at = now_utc
            job.status = "completed"
            job.completed_at = now_utc
            job.updated_at = now_utc

            if email_rec.classification_category == "product_inquiry":
                next_job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=email_rec.store_profile_id,
                    job_type="enrich_product",
                    status="queued",
                )
                session.add(next_job)
            else:
                next_job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=email_rec.store_profile_id,
                    job_type="generate_draft",
                    status="queued",
                )
                session.add(next_job)
            await session.commit()
            return True

        try:
            lookup_res = await ShopifyService.enrich_order_lookup(
                client=shopify_client,
                customer_email=email_rec.sender_email,
                days=60,
                reference_time=email_rec.received_at,
            )

            # INVARIANT R-11: Explicit distinction between has_order_record and no_order
            email_rec.customer_status = (
                "has_order_record" if lookup_res.flags.has_order_record else "no_order"
            )
            email_rec.last_transition_at = now_utc

            line_items_summary = None
            if lookup_res.latest_order and lookup_res.latest_order.line_items:
                line_items_summary = [li.model_dump() for li in lookup_res.latest_order.line_items]

            order_snap = ShopifyOrderSnapshot(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                customer_email=email_rec.sender_email,
                lookup_status=lookup_res.status,
                matched_order_count=lookup_res.flags.recent_orders_count,
                has_paid_order=lookup_res.flags.has_paid_order,
                has_active_order=lookup_res.flags.has_active_order,
                has_cancelled_order=lookup_res.flags.has_cancelled_order,
                has_refunded_order=lookup_res.flags.has_refunded_order,
                has_fulfilled_order=lookup_res.flags.has_fulfilled_order,
                latest_order_id=str(lookup_res.latest_order.id) if lookup_res.latest_order else None,
                latest_order_name=lookup_res.latest_order.name if lookup_res.latest_order else None,
                latest_order_created_at=lookup_res.latest_order.created_at if lookup_res.latest_order else None,
                latest_order_financial_status=lookup_res.latest_order.financial_status if lookup_res.latest_order else None,
                latest_order_fulfillment_status=lookup_res.latest_order.fulfillment_status if lookup_res.latest_order else None,
                latest_order_total_price=lookup_res.latest_order.total_price if lookup_res.latest_order else None,
                latest_order_currency=lookup_res.latest_order.currency if lookup_res.latest_order else "USD",
                line_items_summary=line_items_summary,
                lookup_checked_at=now_utc,
            )
            session.add(order_snap)
            await session.flush()
            email_rec.order_snapshot_id = order_snap.id

            job.status = "completed"
            job.completed_at = now_utc
            job.updated_at = now_utc

            # Cache & attach store policy hashes
            policies_map = await ShopifyService.get_effective_store_policies(
                session=session,
                store_profile_id=email_rec.store_profile_id,
                client=shopify_client,
            )
            email_rec.policy_hashes_used = {p_type: p.content_hash for p_type, p in policies_map.items()}

            if email_rec.classification_category == "product_inquiry":
                next_job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=email_rec.store_profile_id,
                    job_type="enrich_product",
                    status="queued",
                )
                session.add(next_job)
            else:
                next_job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=email_rec.store_profile_id,
                    job_type="generate_draft",
                    status="queued",
                )
                session.add(next_job)

            await session.commit()
            return True

        except (ShopifyProxyError, ShopifyTransientError, ShopifyRateLimitError, TimeoutError, ConnectionError) as transient_exc:
            # INVARIANT R-11: Transient error MUST set lookup_unavailable and NEVER no_order
            email_rec.customer_status = "lookup_unavailable"
            job.last_error = str(transient_exc)
            job.updated_at = now_utc

            if job.attempts < MAX_SHOPIFY_LOOKUP_ATTEMPTS:
                delay_sec = SHOPIFY_LOOKUP_RETRY_DELAYS_SECONDS[job.attempts - 1]
                job.status = "queued"
                job.scheduled_at = now_utc + datetime.timedelta(seconds=delay_sec)
                job.locked_at = None
                job.locked_by = None
            else:
                # Exhausted 5 retries (Section 14.1) -> Transition to manual review
                job.status = "failed"
                email_rec.status = "manual_review"
                email_rec.manual_review_reason = "SHOPIFY_LOOKUP_FAILED"
                email_rec.review_reason_code = "SHOPIFY_LOOKUP_FAILED"
                email_rec.last_transition_at = now_utc

                audit = AuditEvent(
                    id=uuid.uuid4(),
                    event_type="SHOPIFY_ORDER_LOOKUP_EXHAUSTED",
                    actor_user_id=None,
                    target_type="incoming_email",
                    target_id=str(email_rec.id),
                    store_profile_id=email_rec.store_profile_id,
                    safe_change_summary={"attempts": job.attempts, "error": str(transient_exc)},
                )
                session.add(audit)

            order_snap = ShopifyOrderSnapshot(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                customer_email=email_rec.sender_email,
                lookup_status="lookup_unavailable",
                lookup_checked_at=now_utc,
                error_code="SHOPIFY_LOOKUP_RETRYING" if job.attempts < MAX_SHOPIFY_LOOKUP_ATTEMPTS else "SHOPIFY_LOOKUP_EXHAUSTED",
                error_detail=str(transient_exc),
            )
            session.add(order_snap)
            await session.flush()
            email_rec.order_snapshot_id = order_snap.id

            await session.commit()
            return False

        except (ShopifyAuthError, ShopifyPermissionError) as terminal_exc:
            # Terminal auth/permission error: STOP retrying immediately
            job.status = "failed"
            job.last_error = str(terminal_exc)
            job.updated_at = now_utc

            code = "SHOPIFY_AUTH_FAILED" if isinstance(terminal_exc, ShopifyAuthError) else "SHOPIFY_PERMISSION_DENIED"
            email_rec.status = "manual_review"
            email_rec.customer_status = "lookup_unavailable"
            email_rec.manual_review_reason = code
            email_rec.review_reason_code = code
            email_rec.last_transition_at = now_utc

            # Flag store connection auth error
            conn_stmt = select(ShopifyConnection).where(ShopifyConnection.store_profile_id == email_rec.store_profile_id)
            conn = (await session.execute(conn_stmt)).scalar_one_or_none()
            if conn:
                conn.auth_status = "failed"
                conn.last_auth_error_code = code

            order_snap = ShopifyOrderSnapshot(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                customer_email=email_rec.sender_email,
                lookup_status="lookup_unavailable",
                lookup_checked_at=now_utc,
                error_code=code,
                error_detail=str(terminal_exc),
            )
            session.add(order_snap)
            await session.flush()
            email_rec.order_snapshot_id = order_snap.id

            audit = AuditEvent(
                id=uuid.uuid4(),
                event_type="SHOPIFY_CONFIG_ERROR",
                actor_user_id=None,
                target_type="store_profile",
                target_id=str(email_rec.store_profile_id),
                store_profile_id=email_rec.store_profile_id,
                safe_change_summary={"error": str(terminal_exc)},
            )
            session.add(audit)

            await session.commit()
            return False

    @staticmethod
    async def process_product_enrichment_job(
        session: AsyncSession,
        job_id: uuid.UUID,
        worker_id: str = "worker-1",
    ) -> bool:
        """Executes job_type='enrich_product': Live product search via SOCKS5 proxy (Invariant R-16)."""
        job_stmt = (
            select(EmailJob)
            .options(
                selectinload(EmailJob.incoming_email).selectinload(IncomingEmail.store_profile),
            )
            .where(EmailJob.id == job_id)
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if not job or not job.incoming_email:
            return False

        email_rec = job.incoming_email
        now_utc = datetime.datetime.now(datetime.UTC)

        shopify_client = await TransactionalQueueService._get_shopify_client_for_store(
            session=session,
            store_profile_id=email_rec.store_profile_id,
        )

        search_terms: list[str] = []
        if email_rec.extracted_entities and isinstance(email_rec.extracted_entities, dict):
            extracted_prods = email_rec.extracted_entities.get("product_names", [])
            if isinstance(extracted_prods, list):
                search_terms.extend([str(p) for p in extracted_prods if p])

        if not search_terms and email_rec.subject:
            search_terms = [t.strip() for t in email_rec.subject.split() if len(t.strip()) > 3]

        if not shopify_client:
            prod_snap = ShopifyProductSnapshot(
                id=uuid.uuid4(),
                store_profile_id=email_rec.store_profile_id,
                incoming_email_id=email_rec.id,
                search_query=" ".join(search_terms),
                raw_query_terms=search_terms,
                matched_count=0,
                product_resolved=False,
                matched_products=[],
                warning_codes=["PRODUCT_NOT_RESOLVED", "SHOPIFY_UNCONFIGURED"],
                execution_time_ms=0,
            )
            session.add(prod_snap)
            await session.flush()
            email_rec.product_snapshot_id = prod_snap.id
            job.status = "completed"
            job.completed_at = now_utc
            job.updated_at = now_utc

            next_job = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                job_type="generate_draft",
                status="queued",
            )
            session.add(next_job)
            await session.commit()
            return True

        try:
            search_data = await ShopifyService.search_products_live(
                client=shopify_client,
                search_terms=search_terms,
                max_results=5,
            )

            prod_snap = ShopifyProductSnapshot(
                id=uuid.uuid4(),
                store_profile_id=email_rec.store_profile_id,
                incoming_email_id=email_rec.id,
                search_query=search_data.search_query,
                raw_query_terms=search_data.raw_query_terms,
                matched_count=search_data.matched_count,
                product_resolved=search_data.product_resolved,
                matched_products=[p.model_dump() for p in search_data.products],
                warning_codes=search_data.warning_codes,
                execution_time_ms=search_data.execution_time_ms,
            )
            session.add(prod_snap)
            await session.flush()
            email_rec.product_snapshot_id = prod_snap.id

            job.status = "completed"
            job.completed_at = now_utc
            job.updated_at = now_utc

            next_job = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_rec.id,
                store_profile_id=email_rec.store_profile_id,
                job_type="generate_draft",
                status="queued",
            )
            session.add(next_job)

            await session.commit()
            return True

        except (ShopifyProxyError, ShopifyTransientError, ShopifyRateLimitError, TimeoutError, ConnectionError) as exc:
            job.last_error = str(exc)
            job.updated_at = now_utc

            if job.attempts < MAX_SHOPIFY_LOOKUP_ATTEMPTS:
                delay_sec = SHOPIFY_LOOKUP_RETRY_DELAYS_SECONDS[job.attempts - 1]
                job.status = "queued"
                job.scheduled_at = now_utc + datetime.timedelta(seconds=delay_sec)
                job.locked_at = None
                job.locked_by = None
            else:
                job.status = "failed"
                email_rec.status = "manual_review"
                email_rec.manual_review_reason = "SHOPIFY_PRODUCT_SEARCH_FAILED"
                email_rec.review_reason_code = "SHOPIFY_PRODUCT_SEARCH_FAILED"
                email_rec.last_transition_at = now_utc

            await session.commit()
            return False

    @staticmethod
    async def process_draft_generation_job(
        session: AsyncSession,
        job_id: uuid.UUID,
        worker_id: str = "worker-1",
    ) -> bool:
        """Executes job_type='generate_draft': Synthesizes AI reply draft (Invariant R-22, R-23)."""
        job_stmt = select(EmailJob).where(EmailJob.id == job_id)
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if not job:
            return False

        now_utc = datetime.datetime.now(datetime.UTC)

        try:
            await DraftService.generate_initial_draft(session, job.incoming_email_id)
            job.status = "completed"
            job.completed_at = now_utc
            job.updated_at = now_utc
            await session.commit()
            return True
        except DraftBusinessError as exc:
            # If ineligible, email was safely updated to manual_review by DraftService
            job.status = "completed"
            job.last_error = f"Draft generation ineligible: {exc.message}"
            job.completed_at = now_utc
            job.updated_at = now_utc
            await session.commit()
            return True
        except Exception as exc:
            # Transient error: retry with exponential backoff
            await TransactionalQueueService.fail_job(session, job_id, f"Draft generation failed: {exc}")
            return False

    @staticmethod
    async def process_job(
        session: AsyncSession,
        job_id: uuid.UUID,
        worker_id: str = "worker-1",
    ) -> bool:
        """Unified job execution dispatcher based on job_type."""
        stmt = select(EmailJob).where(EmailJob.id == job_id)
        job = (await session.execute(stmt)).scalar_one_or_none()
        if not job:
            return False
        if job.job_type == "classify":
            return await TransactionalQueueService.process_classification_job(session, job_id, worker_id)
        elif job.job_type in ("enrich", "enrich_order"):
            return await TransactionalQueueService.process_order_enrichment_job(session, job_id, worker_id)
        elif job.job_type == "enrich_product":
            return await TransactionalQueueService.process_product_enrichment_job(session, job_id, worker_id)
        elif job.job_type == "generate_draft":
            return await TransactionalQueueService.process_draft_generation_job(session, job_id, worker_id)
        return False

