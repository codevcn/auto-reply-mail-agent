"""Retention Service implementing 120-Day Retention & Safe Batch Cleanup (Invariant R-34)."""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
from app.retention.schemas import (
    RetentionCleanupReport,
    RetentionDeletedCounts,
    RetentionStatusResponse,
)

logger = logging.getLogger("mail_agent.retention")


def _get_rowcount(res: Any) -> int:
    """Safely extracts deleted rowcount across database engines."""
    return int(getattr(res, "rowcount", 0) or 0)


class RetentionService:
    """Core domain service implementing 120-Day Retention & Safe Batch Cleanup (R-34)."""

    _last_report: RetentionCleanupReport | None = None

    @classmethod
    def calculate_cutoff_date(cls, retention_days: int) -> datetime.datetime:
        """Returns UTC cutoff timestamp: now - timedelta(days=retention_days).

        Enforces Invariant R-34: clamped strictly between 1 and 120 days.
        """
        clamped_days = min(max(retention_days, 1), 120)
        return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=clamped_days)

    @classmethod
    async def run_cleanup_job(
        cls,
        db: AsyncSession,
        retention_days: int = 120,
        dry_run: bool = False,
        batch_size: int = 500,
        actor_user_id: uuid.UUID | None = None,
    ) -> RetentionCleanupReport:
        """Executes full retention cleanup routine with referential integrity guarantee.

        Adheres strictly to Invariant R-34 (max 120 days), R-04, and R-28.
        Preserves store profiles, proxies, credentials, users, checkpoints, policy cache.
        """
        start_time = time.perf_counter()
        clamped_days = min(max(retention_days, 1), 120)
        cutoff_date = cls.calculate_cutoff_date(clamped_days)
        counts = RetentionDeletedCounts()

        if dry_run:
            # DRY-RUN MODE: execute SELECT COUNT(*) without modifying database state
            expired_email_subquery = (
                select(IncomingEmail.id)
                .where(IncomingEmail.created_at < cutoff_date)
                .scalar_subquery()
            )

            email_count_res = await db.execute(
                select(func.count(IncomingEmail.id)).where(IncomingEmail.created_at < cutoff_date)
            )
            counts.incoming_emails = email_count_res.scalar_one() or 0

            if counts.incoming_emails > 0:
                drafts_res = await db.execute(
                    select(func.count(ReplyDraft.id)).where(
                        ReplyDraft.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.reply_drafts = drafts_res.scalar_one() or 0

                versions_res = await db.execute(
                    select(func.count(ReplyDraftVersion.id)).where(
                        ReplyDraftVersion.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.reply_draft_versions = versions_res.scalar_one() or 0

                attempts_res = await db.execute(
                    select(func.count(ReplyDeliveryAttempt.id)).where(
                        ReplyDeliveryAttempt.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.reply_delivery_attempts = attempts_res.scalar_one() or 0

                orders_res = await db.execute(
                    select(func.count(ShopifyOrderSnapshot.id)).where(
                        ShopifyOrderSnapshot.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.shopify_order_snapshots = orders_res.scalar_one() or 0

                products_res = await db.execute(
                    select(func.count(ShopifyProductSnapshot.id)).where(
                        ShopifyProductSnapshot.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.shopify_product_snapshots = products_res.scalar_one() or 0

                class_res = await db.execute(
                    select(func.count(EmailClassification.id)).where(
                        EmailClassification.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.email_classifications = class_res.scalar_one() or 0

                attach_res = await db.execute(
                    select(func.count(EmailAttachmentMetadata.id)).where(
                        EmailAttachmentMetadata.incoming_email_id.in_(expired_email_subquery)
                    )
                )
                counts.email_attachment_metadata = attach_res.scalar_one() or 0

            # Count expired completed/failed jobs
            jobs_res = await db.execute(
                select(func.count(EmailJob.id)).where(
                    (EmailJob.incoming_email_id.in_(expired_email_subquery))
                    | (
                        EmailJob.status.in_(["completed", "failed"])
                        & (EmailJob.created_at < cutoff_date)
                    )
                )
            )
            counts.email_jobs = jobs_res.scalar_one() or 0

            # Count expired audit events
            audit_res = await db.execute(
                select(func.count(AuditEvent.id)).where(AuditEvent.created_at < cutoff_date)
            )
            counts.audit_events = audit_res.scalar_one() or 0

        else:
            # LIVE DELETION MODE: 11-step safe batch deletion
            batch_limit = max(batch_size, 1)

            # Step Group 1: Email Workflow Deletion in Batches
            while True:
                batch_stmt = (
                    select(IncomingEmail.id)
                    .where(IncomingEmail.created_at < cutoff_date)
                    .order_by(IncomingEmail.created_at.asc())
                    .limit(batch_limit)
                )
                batch_ids = list((await db.execute(batch_stmt)).scalars().all())
                if not batch_ids:
                    break

                # 1. reply_delivery_attempts
                res1 = await db.execute(
                    delete(ReplyDeliveryAttempt).where(
                        ReplyDeliveryAttempt.incoming_email_id.in_(batch_ids)
                    )
                )
                counts.reply_delivery_attempts += _get_rowcount(res1)

                # 2. Break circular pointer in reply_drafts
                await db.execute(
                    update(ReplyDraft)
                    .where(ReplyDraft.incoming_email_id.in_(batch_ids))
                    .values(current_version_id=None)
                )

                # 3. reply_draft_versions
                res3 = await db.execute(
                    delete(ReplyDraftVersion).where(
                        ReplyDraftVersion.incoming_email_id.in_(batch_ids)
                    )
                )
                counts.reply_draft_versions += _get_rowcount(res3)

                # 4. reply_drafts
                res4 = await db.execute(
                    delete(ReplyDraft).where(ReplyDraft.incoming_email_id.in_(batch_ids))
                )
                counts.reply_drafts += _get_rowcount(res4)

                # 5. Break snapshot pointers in incoming_emails
                await db.execute(
                    update(IncomingEmail)
                    .where(IncomingEmail.id.in_(batch_ids))
                    .values(order_snapshot_id=None, product_snapshot_id=None)
                )

                # 6. shopify_order_snapshots
                res6 = await db.execute(
                    delete(ShopifyOrderSnapshot).where(
                        ShopifyOrderSnapshot.incoming_email_id.in_(batch_ids)
                    )
                )
                counts.shopify_order_snapshots += _get_rowcount(res6)

                # 7. shopify_product_snapshots
                res7 = await db.execute(
                    delete(ShopifyProductSnapshot).where(
                        ShopifyProductSnapshot.incoming_email_id.in_(batch_ids)
                    )
                )
                counts.shopify_product_snapshots += _get_rowcount(res7)

                # 8. email_classifications
                res8 = await db.execute(
                    delete(EmailClassification).where(
                        EmailClassification.incoming_email_id.in_(batch_ids)
                    )
                )
                counts.email_classifications += _get_rowcount(res8)

                # 9. email_attachment_metadata
                res9 = await db.execute(
                    delete(EmailAttachmentMetadata).where(
                        EmailAttachmentMetadata.incoming_email_id.in_(batch_ids)
                    )
                )
                counts.email_attachment_metadata += _get_rowcount(res9)

                # 10. email_jobs linked to email
                res10 = await db.execute(
                    delete(EmailJob).where(EmailJob.incoming_email_id.in_(batch_ids))
                )
                counts.email_jobs += _get_rowcount(res10)

                # 11. incoming_emails
                res11 = await db.execute(
                    delete(IncomingEmail).where(IncomingEmail.id.in_(batch_ids))
                )
                counts.incoming_emails += _get_rowcount(res11)

                # Release transaction lock
                await db.commit()
                await asyncio.sleep(0.005)

            # Step Group 2: Standalone completed/failed jobs
            while True:
                job_stmt = (
                    select(EmailJob.id)
                    .where(
                        EmailJob.status.in_(["completed", "failed"]),
                        EmailJob.created_at < cutoff_date,
                    )
                    .limit(batch_limit)
                )
                job_ids = list((await db.execute(job_stmt)).scalars().all())
                if not job_ids:
                    break
                job_del_res = await db.execute(delete(EmailJob).where(EmailJob.id.in_(job_ids)))
                counts.email_jobs += _get_rowcount(job_del_res)
                await db.commit()
                await asyncio.sleep(0.005)

            # Step Group 3: Expired Audit Events
            while True:
                audit_stmt = (
                    select(AuditEvent.id)
                    .where(AuditEvent.created_at < cutoff_date)
                    .limit(batch_limit)
                )
                audit_ids = list((await db.execute(audit_stmt)).scalars().all())
                if not audit_ids:
                    break
                audit_del_res = await db.execute(
                    delete(AuditEvent).where(AuditEvent.id.in_(audit_ids))
                )
                counts.audit_events += _get_rowcount(audit_del_res)
                await db.commit()
                await asyncio.sleep(0.005)

            # Audit record for the retention execution
            audit_entry = AuditEvent(
                actor_user_id=actor_user_id,
                event_type="RETENTION_CLEANUP_EXECUTED",
                target_type="system",
                target_id="retention",
                safe_change_summary={
                    "retention_days": clamped_days,
                    "cutoff_date": cutoff_date.isoformat(),
                    "dry_run": False,
                    "deleted_counts": counts.model_dump(),
                    "total_deleted": sum(counts.model_dump().values()),
                },
            )
            db.add(audit_entry)
            await db.commit()

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        total_affected = sum(counts.model_dump().values())

        # Zero-PII aggregate logging
        logger.info(
            "Retention cleanup %s (days=%d, cutoff=%s, total=%d)",
            "simulated" if dry_run else "completed",
            clamped_days,
            cutoff_date.isoformat(),
            total_affected,
            extra={
                "retention_days": clamped_days,
                "cutoff_date": cutoff_date.isoformat(),
                "dry_run": dry_run,
                "duration_ms": elapsed_ms,
                "deleted_counts": counts.model_dump(),
                "total_records": total_affected,
            },
        )

        report = RetentionCleanupReport(
            timestamp=datetime.datetime.now(datetime.UTC),
            retention_days=clamped_days,
            cutoff_date=cutoff_date,
            dry_run=dry_run,
            duration_ms=round(elapsed_ms, 2),
            deleted_counts=counts,
            total_records_affected=total_affected,
            status="completed",
        )
        cls._last_report = report
        return report

    @classmethod
    async def get_retention_status(
        cls,
        db: AsyncSession,
    ) -> RetentionStatusResponse:
        """Inspects DB to count pending expired records without modifying state."""
        settings = get_settings()
        clamped_days = min(max(settings.RETENTION_DAYS, 1), 120)
        cutoff_date = cls.calculate_cutoff_date(clamped_days)

        email_count_res = await db.execute(
            select(func.count(IncomingEmail.id)).where(IncomingEmail.created_at < cutoff_date)
        )
        pending_emails = email_count_res.scalar_one() or 0

        audit_count_res = await db.execute(
            select(func.count(AuditEvent.id)).where(AuditEvent.created_at < cutoff_date)
        )
        pending_audit = audit_count_res.scalar_one() or 0

        return RetentionStatusResponse(
            configured_retention_days=clamped_days,
            current_cutoff_date=cutoff_date,
            batch_size=settings.RETENTION_BATCH_SIZE,
            scheduler_enabled=True,
            scheduler_interval_hours=settings.RETENTION_SCHEDULER_INTERVAL_HOURS,
            pending_expired_emails_count=pending_emails,
            pending_expired_audit_events_count=pending_audit,
            last_cleanup_report=cls._last_report,
        )
