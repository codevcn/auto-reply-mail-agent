"""Draft Service: Intent-Based Draft Rules, Immutable Versioning, and Regeneration."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.schemas import ClassificationResult, DraftGenerationInput, StoreContext
from app.ai.service import get_ai_provider
from app.db.models.audit import AuditEvent
from app.db.models.draft import ReplyDraft, ReplyDraftVersion
from app.db.models.email import IncomingEmail
from app.db.models.store import StorePolicy, StoreProfile
from app.draft.prompt import (
    compute_content_hash,
    format_plain_to_html,
    resolve_draft_language,
)
from app.draft.schemas import DraftVersionDTO, ReplyDraftDetailResponse


class DraftBusinessError(Exception):
    """Business exception for draft operations."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class DraftService:
    """Core service for managing reply drafts, version history, and regeneration."""

    @staticmethod
    def evaluate_draft_eligibility(email: IncomingEmail) -> tuple[bool, str | None, list[str]]:
        """Evaluates whether an incoming email is eligible for automated reply drafting.

        Adheres strictly to Invariants R-22, R-23, R-08, and R-21:
        - Eligible: product_inquiry, order_support, complaint, return_or_refund
        - Ineligible: spam, partnership, other, uncertain, prompt injection, attachment failure, shopify lookup failure
        """
        # 1. Spam check (Invariant R-08)
        if email.status == "spam" or email.spam_status == "spam":
            return False, "SPAM_DETECTED", ["SPAM_DETECTED"]

        # 2. Prompt Injection Quarantine
        if (
            email.review_reason_code == "PROMPT_INJECTION_DETECTED"
            or email.manual_review_reason == "PROMPT_INJECTION_DETECTED"
        ):
            return False, "PROMPT_INJECTION_DETECTED", ["PROMPT_INJECTION_DETECTED"]

        # 3. Attachment Failure check (Invariant R-21)
        if email.manual_review_reason in (
            "ATTACHMENT_FAILED",
            "ATTACHMENT_UNREADABLE",
            "ATTACHMENT_TOO_LARGE",
        ):
            return False, email.manual_review_reason, ["ATTACHMENT_FAILED"]

        # 4. Shopify Enrichment Failures
        if email.manual_review_reason in (
            "SHOPIFY_LOOKUP_FAILED",
            "SHOPIFY_PRODUCT_SEARCH_FAILED",
            "SHOPIFY_AUTH_FAILED",
            "SHOPIFY_CONFIG_ERROR",
        ):
            return False, email.manual_review_reason, [email.manual_review_reason]

        # 5. Intent Category & Confidence check (Invariant R-22, R-23)
        intent = email.classification_category or "uncertain"
        confidence = email.intent_confidence if email.intent_confidence is not None else 1.0

        if intent in ("partnership", "other", "uncertain") or confidence < 0.70:
            reason = f"INTENT_REQUIRES_MANUAL_REVIEW:{intent.upper()}"
            return False, reason, [f"INTENT_{intent.upper()}"]

        # 6. Eligible Drafting Intents
        if intent in ("product_inquiry", "order_support", "complaint", "return_or_refund"):
            warnings: list[str] = []
            if intent == "complaint":
                warnings.append("COMPLAINT_DETECTED")
            elif intent == "return_or_refund":
                warnings.append("RETURN_OR_REFUND_REQUESTED")

            if intent in ("order_support", "recent_order") and email.customer_status != "has_order_record":
                warnings.append("NO_RECENT_ORDER")

            # Check product snapshot resolved status if inquiry
            if intent == "product_inquiry" and email.product_snapshots:
                latest_ps = email.product_snapshots[-1]
                if not latest_ps.product_resolved:
                    warnings.append("PRODUCT_NOT_RESOLVED")

            return True, None, warnings

        return False, "UNKNOWN_INTENT_CATEGORY", ["UNKNOWN_INTENT"]

    @staticmethod
    async def generate_initial_draft(
        session: AsyncSession,
        email_id: uuid.UUID,
    ) -> ReplyDraft:
        """Generates the initial AI reply draft (Version 1) for an eligible email."""
        stmt = (
            select(IncomingEmail)
            .options(
                selectinload(IncomingEmail.store_profile).selectinload(StoreProfile.policies),
                selectinload(IncomingEmail.order_snapshots),
                selectinload(IncomingEmail.product_snapshots),
                selectinload(IncomingEmail.classifications),
            )
            .where(IncomingEmail.id == email_id)
        )
        email = (await session.execute(stmt)).scalar_one_or_none()
        if not email:
            raise DraftBusinessError("EMAIL_NOT_FOUND", "Email not found", 404)

        now_utc = datetime.datetime.now(datetime.UTC)

        # Check eligibility
        is_eligible, ineligibility_reason, warning_codes = DraftService.evaluate_draft_eligibility(email)
        if not is_eligible:
            email.status = "manual_review"
            email.manual_review_reason = ineligibility_reason
            email.review_reason_code = ineligibility_reason
            email.last_transition_at = now_utc
            await session.commit()
            raise DraftBusinessError(
                "INELIGIBLE_FOR_DRAFT",
                f"Email is not eligible for automated draft: {ineligibility_reason}",
                400,
            )

        store = email.store_profile
        order_snap = email.order_snapshots[-1] if email.order_snapshots else None
        prod_snap = email.product_snapshots[-1] if email.product_snapshots else None

        # Build policies dictionary
        policies_dict: dict[str, str] = {}
        if store and store.policies:
            for p in store.policies:
                policies_dict[p.policy_type.lower()] = p.body_text

        # Determine target language
        target_lang = resolve_draft_language(
            requested_language=None,
            detected_language=email.detected_language,
            store_default_language=store.default_language if store else "en",
        )

        # Build context for AI
        class_res = ClassificationResult(
            is_spam=False,
            spam_status="not_spam",
            intent=email.classification_category or "product_inquiry",  # type: ignore
            order_status=email.customer_status or "no_order",  # type: ignore
            detected_language=email.detected_language or target_lang,
            requires_manual_review=False,
        )
        # Fetch actual customer body text on-demand from IMAP PEEK (R-04)
        customer_body = email.subject or ""
        try:
            from app.ingestion.service import MailFetchService
            fetched = await MailFetchService.fetch_email_content(session, email.id)
            if fetched and fetched.body_text:
                customer_body = fetched.body_text
        except Exception:
            pass

        draft_input = DraftGenerationInput(
            email_id=email.id,
            subject=email.subject or "Support Inquiry",
            body_text=customer_body,
            classification=class_res,
            order_snapshot=order_snap.__dict__ if order_snap else None,
            product_snapshot=prod_snap.__dict__ if prod_snap else None,
            policies=policies_dict,
            target_language=target_lang,
        )
        store_ctx = StoreContext(
            store_profile_id=store.id,
            brand_name=store.name,
            public_domain=store.public_domain,
            canonical_domain=store.canonical_domain,
            default_language=store.default_language or "en",
            tone_of_voice=store.tone_of_voice or "Professional, helpful, empathetic",
            forbidden_claims=store.forbidden_claims or [],
        )

        ai_provider = get_ai_provider()
        draft_result = await ai_provider.generate_reply_draft(draft_input, store_ctx)

        # Merge warning codes
        combined_warnings = list(dict.fromkeys(warning_codes + draft_result.warning_codes))

        # Check existing draft container or create new
        draft_stmt = select(ReplyDraft).where(ReplyDraft.incoming_email_id == email.id)
        reply_draft = (await session.execute(draft_stmt)).scalar_one_or_none()
        if not reply_draft:
            reply_draft = ReplyDraft(
                id=uuid.uuid4(),
                incoming_email_id=email.id,
                store_profile_id=store.id,
                current_version_number=1,
                status="pending_approval",
                created_at=now_utc,
                updated_at=now_utc,
            )
            session.add(reply_draft)
            await session.flush()

        content_hash = compute_content_hash(draft_result.subject, draft_result.body_text)

        # Create Version 1 (Immutable)
        version = ReplyDraftVersion(
            id=uuid.uuid4(),
            draft_id=reply_draft.id,
            incoming_email_id=email.id,
            version_number=1,
            subject=draft_result.subject,
            body_text=draft_result.body_text,
            body_html=draft_result.body_html,
            language=draft_result.language,
            source="ai",
            ai_provider=draft_result.provider_type,
            ai_model=draft_result.model_identifier,
            prompt_version="v1.0",
            order_snapshot_id=order_snap.id if order_snap else None,
            product_snapshot_id=prod_snap.id if prod_snap else None,
            policy_hashes_used=email.policy_hashes_used,
            warning_codes=combined_warnings,
            content_hash=content_hash,
            is_current_version=True,
            created_at=now_utc,
        )
        session.add(version)
        await session.flush()

        reply_draft.current_version_id = version.id
        reply_draft.current_version_number = 1
        reply_draft.status = "pending_approval"
        reply_draft.updated_at = now_utc

        email.status = "pending_approval"
        email.current_draft_version = 1
        email.last_transition_at = now_utc

        await session.commit()
        return reply_draft

    @staticmethod
    async def save_user_version(
        session: AsyncSession,
        email_id: uuid.UUID,
        subject: str,
        body_text: str,
        body_html: str | None,
        user_id: uuid.UUID | None,
    ) -> ReplyDraftVersion:
        """Saves a user edit as a new immutable draft version (Invariant R-24)."""
        draft_stmt = (
            select(ReplyDraft)
            .options(selectinload(ReplyDraft.versions))
            .where(ReplyDraft.incoming_email_id == email_id)
            .with_for_update()
        )
        draft = (await session.execute(draft_stmt)).scalar_one_or_none()
        if not draft:
            raise DraftBusinessError("DRAFT_NOT_FOUND", "Reply draft does not exist for email", 404)

        if draft.status in ("sent", "sending"):
            raise DraftBusinessError("DRAFT_LOCKED", "Cannot edit draft in sending/sent state", 409)

        now_utc = datetime.datetime.now(datetime.UTC)

        # Mark all previous versions is_current_version = False
        for v in draft.versions:
            v.is_current_version = False

        new_version_num = draft.current_version_number + 1
        content_hash = compute_content_hash(subject, body_text)
        html_content = body_html if body_html else format_plain_to_html(body_text)

        # Inherit current language and warnings if available
        curr_lang = "en"
        curr_warnings: list[str] = []
        curr_order_id = None
        curr_prod_id = None
        curr_policy_hashes = None
        if draft.versions:
            latest = sorted(draft.versions, key=lambda x: x.version_number)[-1]
            curr_lang = latest.language
            curr_warnings = latest.warning_codes or []
            curr_order_id = latest.order_snapshot_id
            curr_prod_id = latest.product_snapshot_id
            curr_policy_hashes = latest.policy_hashes_used

        new_version = ReplyDraftVersion(
            id=uuid.uuid4(),
            draft_id=draft.id,
            incoming_email_id=email_id,
            version_number=new_version_num,
            subject=subject.strip(),
            body_text=body_text.strip(),
            body_html=html_content,
            language=curr_lang,
            source="user",
            created_by=user_id,
            order_snapshot_id=curr_order_id,
            product_snapshot_id=curr_prod_id,
            policy_hashes_used=curr_policy_hashes,
            warning_codes=curr_warnings,
            content_hash=content_hash,
            is_current_version=True,
            created_at=now_utc,
        )
        session.add(new_version)
        await session.flush()

        draft.current_version_id = new_version.id
        draft.current_version_number = new_version_num
        draft.row_version += 1
        draft.updated_at = now_utc

        # Update IncomingEmail
        email_stmt = select(IncomingEmail).where(IncomingEmail.id == email_id)
        email = (await session.execute(email_stmt)).scalar_one_or_none()
        if email:
            email.current_draft_version = new_version_num
            email.last_transition_at = now_utc

        # Record AuditEvent
        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                event_type="DRAFT_VERSION_CREATED",
                actor_user_id=user_id,
                target_type="reply_draft",
                target_id=str(draft.id),
                store_profile_id=draft.store_profile_id,
                safe_change_summary={
                    "version_number": new_version_num,
                    "source": "user",
                    "subject": subject,
                },
            )
        )

        await session.commit()
        return new_version

    @staticmethod
    async def regenerate_draft(
        session: AsyncSession,
        email_id: uuid.UUID,
        target_language: str | None = None,
        custom_instructions: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> ReplyDraftVersion:
        """Regenerates reply draft using AI with specified language or policies (Invariant R-24)."""
        stmt = (
            select(IncomingEmail)
            .options(
                selectinload(IncomingEmail.store_profile).selectinload(StoreProfile.policies),
                selectinload(IncomingEmail.order_snapshots),
                selectinload(IncomingEmail.product_snapshots),
                selectinload(IncomingEmail.classifications),
            )
            .where(IncomingEmail.id == email_id)
        )
        email = (await session.execute(stmt)).scalar_one_or_none()
        if not email:
            raise DraftBusinessError("EMAIL_NOT_FOUND", "Email not found", 404)

        draft_stmt = (
            select(ReplyDraft)
            .options(selectinload(ReplyDraft.versions))
            .where(ReplyDraft.incoming_email_id == email_id)
            .with_for_update()
        )
        draft = (await session.execute(draft_stmt)).scalar_one_or_none()
        if not draft:
            raise DraftBusinessError("DRAFT_NOT_FOUND", "Draft container does not exist", 404)

        if draft.status in ("sent", "sending"):
            raise DraftBusinessError("DRAFT_LOCKED", "Cannot regenerate draft in sending/sent state", 409)

        store = email.store_profile
        order_snap = email.order_snapshots[-1] if email.order_snapshots else None
        prod_snap = email.product_snapshots[-1] if email.product_snapshots else None

        # Build policies dictionary & latest hashes
        policies_dict: dict[str, str] = {}
        latest_policy_hashes: dict[str, str] = {}
        if store and store.policies:
            for p in store.policies:
                policies_dict[p.policy_type.lower()] = p.body_text
                latest_policy_hashes[p.policy_type.upper()] = p.content_hash

        resolved_lang = resolve_draft_language(
            requested_language=target_language,
            detected_language=email.detected_language,
            store_default_language=store.default_language if store else "en",
        )

        class_res = ClassificationResult(
            is_spam=False,
            spam_status="not_spam",
            intent=email.classification_category or "product_inquiry",  # type: ignore
            order_status=email.customer_status or "no_order",  # type: ignore
            detected_language=resolved_lang,
            requires_manual_review=False,
        )
        # Fetch actual customer body text on-demand from IMAP PEEK (R-04)
        customer_body = email.subject or ""
        try:
            from app.ingestion.service import MailFetchService
            fetched = await MailFetchService.fetch_email_content(session, email.id)
            if fetched and fetched.body_text:
                customer_body = fetched.body_text
        except Exception:
            pass

        draft_input = DraftGenerationInput(
            email_id=email.id,
            subject=email.subject or "Support Inquiry",
            body_text=customer_body,
            classification=class_res,
            order_snapshot=order_snap.__dict__ if order_snap else None,
            product_snapshot=prod_snap.__dict__ if prod_snap else None,
            policies=policies_dict,
            target_language=resolved_lang,
        )
        store_ctx = StoreContext(
            store_profile_id=store.id,
            brand_name=store.name,
            public_domain=store.public_domain,
            canonical_domain=store.canonical_domain,
            default_language=store.default_language or "en",
            tone_of_voice=store.tone_of_voice or "Professional, helpful, empathetic",
            forbidden_claims=store.forbidden_claims or [],
        )

        ai_provider = get_ai_provider()
        draft_result = await ai_provider.generate_reply_draft(draft_input, store_ctx)

        now_utc = datetime.datetime.now(datetime.UTC)

        # Mark previous versions is_current_version = False
        for v in draft.versions:
            v.is_current_version = False

        new_version_num = draft.current_version_number + 1
        content_hash = compute_content_hash(draft_result.subject, draft_result.body_text)

        # Evaluate base eligibility warnings
        _, _, base_warnings = DraftService.evaluate_draft_eligibility(email)
        combined_warnings = list(dict.fromkeys(base_warnings + draft_result.warning_codes))

        new_version = ReplyDraftVersion(
            id=uuid.uuid4(),
            draft_id=draft.id,
            incoming_email_id=email_id,
            version_number=new_version_num,
            subject=draft_result.subject,
            body_text=draft_result.body_text,
            body_html=draft_result.body_html,
            language=draft_result.language,
            source="ai",
            ai_provider=draft_result.provider_type,
            ai_model=draft_result.model_identifier,
            prompt_version="v1.0",
            order_snapshot_id=order_snap.id if order_snap else None,
            product_snapshot_id=prod_snap.id if prod_snap else None,
            policy_hashes_used=latest_policy_hashes or email.policy_hashes_used,
            warning_codes=combined_warnings,
            content_hash=content_hash,
            is_current_version=True,
            created_by=user_id,
            created_at=now_utc,
        )
        session.add(new_version)
        await session.flush()

        draft.current_version_id = new_version.id
        draft.current_version_number = new_version_num
        draft.row_version += 1
        draft.updated_at = now_utc

        # If email was marked stale, regeneration with latest policies clears stale status
        email.policy_hashes_used = latest_policy_hashes or email.policy_hashes_used
        email.is_stale = False
        email.stale_reason = None
        email.stale_details = None
        email.current_draft_version = new_version_num
        email.last_transition_at = now_utc

        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                event_type="DRAFT_REGENERATED",
                actor_user_id=user_id,
                target_type="reply_draft",
                target_id=str(draft.id),
                store_profile_id=draft.store_profile_id,
                safe_change_summary={
                    "version_number": new_version_num,
                    "target_language": resolved_lang,
                    "model": draft_result.model_identifier,
                },
            )
        )

        await session.commit()
        return new_version

    @staticmethod
    async def get_draft_detail(
        session: AsyncSession,
        email_id: uuid.UUID,
    ) -> ReplyDraftDetailResponse:
        """Retrieves draft container, current version, full version history, and stale policy status."""
        stmt = (
            select(ReplyDraft)
            .options(
                selectinload(ReplyDraft.versions),
                selectinload(ReplyDraft.incoming_email).selectinload(IncomingEmail.store_profile).selectinload(StoreProfile.policies),
            )
            .where(ReplyDraft.incoming_email_id == email_id)
        )
        draft = (await session.execute(stmt)).scalar_one_or_none()
        if not draft:
            raise DraftBusinessError("DRAFT_NOT_FOUND", "No draft found for this email", 404)

        # Check stale policy status
        is_stale, stale_reason, stale_details = await DraftService.check_stale_policy(session, draft)

        sorted_versions = sorted(draft.versions, key=lambda v: v.version_number)
        current_v = next((v for v in sorted_versions if v.id == draft.current_version_id), None)
        if not current_v and sorted_versions:
            current_v = sorted_versions[-1]

        warning_codes = current_v.warning_codes if current_v else []

        return ReplyDraftDetailResponse(
            draft_id=draft.id,
            incoming_email_id=draft.incoming_email_id,
            store_profile_id=draft.store_profile_id,
            status=draft.status,
            current_version_number=draft.current_version_number,
            current_version=DraftVersionDTO.model_validate(current_v) if current_v else None,
            versions=[DraftVersionDTO.model_validate(v) for v in sorted_versions],
            is_stale=is_stale,
            stale_reason=stale_reason,
            stale_details=stale_details,
            warning_codes=warning_codes,
        )

    @staticmethod
    async def check_stale_policy(
        session: AsyncSession,
        draft: ReplyDraft,
    ) -> tuple[bool, str | None, list[dict[str, str]] | None]:
        """Checks if store policies have changed since current draft version was generated (Invariant R-18)."""
        if not draft.current_version_id:
            return False, None, None

        current_v = await session.get(ReplyDraftVersion, draft.current_version_id)
        if not current_v or not current_v.policy_hashes_used:
            return False, None, None

        used_hashes = current_v.policy_hashes_used
        policies_stmt = select(StorePolicy).where(StorePolicy.store_profile_id == draft.store_profile_id)
        current_policies = (await session.execute(policies_stmt)).scalars().all()

        stale_details: list[dict[str, str]] = []
        for p in current_policies:
            p_type = p.policy_type.upper()
            if p_type in used_hashes and used_hashes[p_type] != p.content_hash:
                stale_details.append({
                    "policy_type": p_type,
                    "used_hash": used_hashes[p_type],
                    "current_hash": p.content_hash,
                })

        if stale_details:
            return True, "STORE_POLICY_UPDATED", stale_details

        return False, None, None

    @staticmethod
    async def reject_draft(
        session: AsyncSession,
        email_id: uuid.UUID,
        reason: str | None,
        action_type: str,
        user_id: uuid.UUID | None,
    ) -> bool:
        """Rejects draft, marking email as 'no_reply_needed' or 'manual_review'."""
        email_stmt = (
            select(IncomingEmail)
            .options(selectinload(IncomingEmail.reply_draft_rel))
            .where(IncomingEmail.id == email_id)
            .with_for_update()
        )
        email = (await session.execute(email_stmt)).scalar_one_or_none()
        if not email:
            raise DraftBusinessError("EMAIL_NOT_FOUND", "Email not found", 404)

        now_utc = datetime.datetime.now(datetime.UTC)
        new_status = "no_reply_needed" if action_type == "no_reply_needed" else "manual_review"

        email.status = new_status
        email.last_transition_at = now_utc
        if reason:
            email.manual_review_reason = reason

        draft_stmt = select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id)
        draft = (await session.execute(draft_stmt)).scalar_one_or_none()
        if draft:
            draft.status = "rejected" if action_type == "rejected" else new_status
            draft.updated_at = now_utc

        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                event_type="DRAFT_REJECTED",
                actor_user_id=user_id,
                target_type="incoming_email",
                target_id=str(email.id),
                store_profile_id=email.store_profile_id,
                safe_change_summary={"action_type": action_type, "reason": reason},
            )
        )

        await session.commit()
        return True
