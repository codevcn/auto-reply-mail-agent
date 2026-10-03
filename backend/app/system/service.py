"""System Health and Observability Service (Phase 7)."""

from __future__ import annotations

import datetime
import logging
import time
import uuid
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.audit import AuditEvent
from app.db.models.email import EmailJob, IncomingEmail, MailboxCheckpoint
from app.db.models.shopify import ShopifyOrderSnapshot
from app.db.models.store import ProxyProfile, StoreProfile
from app.system.schemas import (
    AuditEventItem,
    AuditEventsResponse,
    DatabaseHealth,
    LivenessResponse,
    MailboxHealth,
    PoliciesHealth,
    ProxyHealth,
    QueueMetrics,
    ReadinessResponse,
    ShopifyConnectionHealth,
    StoreHealthItem,
    SystemHealthSummaryResponse,
    WorkerHealth,
)

logger = logging.getLogger("mail_agent.system")

_START_TIME: float = time.time()


def _mask_host(host: str, port: int) -> str:
    """Masks IP address or hostname to protect confidential network infrastructure."""
    parts = host.split(".")
    if len(parts) == 4:
        # IPv4: keep first 2 octets, mask last 2
        return f"{parts[0]}.{parts[1]}.***.***:{port}"
    if len(host) > 6:
        return f"{host[:3]}***{host[-3:]}:{port}"
    return f"***:{port}"


class SystemHealthService:
    """Provides liveness, readiness, full-system health summary, and audit feed."""

    @classmethod
    def get_uptime_seconds(cls) -> float:
        """Calculates process uptime in seconds."""
        return time.time() - _START_TIME

    @classmethod
    def check_liveness(cls) -> LivenessResponse:
        """Fast liveness check for container orchestrator."""
        return LivenessResponse(
            status="alive",
            timestamp=datetime.datetime.now(datetime.UTC),
            uptime_seconds=round(cls.get_uptime_seconds(), 2),
        )

    @classmethod
    async def check_readiness(cls, db: AsyncSession) -> tuple[int, ReadinessResponse]:
        """Readiness check that verifies database connectivity."""
        now_utc = datetime.datetime.now(datetime.UTC)
        start = time.perf_counter()
        try:
            await db.execute(text("SELECT 1"))
            latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
            dialect = "postgresql"
            if db.bind and hasattr(db.bind, "dialect") and db.bind.dialect.name:
                dialect = db.bind.dialect.name
            return 200, ReadinessResponse(
                status="ready",
                database=DatabaseHealth(
                    status="connected",
                    latency_ms=latency_ms,
                    dialect=dialect,
                ),
                timestamp=now_utc,
            )
        except Exception as e:
            logger.error("Readiness check database ping failed: %s", e)
            return 503, ReadinessResponse(
                status="unready",
                database=DatabaseHealth(
                    status="disconnected",
                    latency_ms=0.0,
                    dialect="postgresql",
                ),
                timestamp=now_utc,
            )

    @classmethod
    async def get_health_summary(cls, db: AsyncSession) -> SystemHealthSummaryResponse:
        """Compiles complete operational health status across 4 stores, queues, proxy, and worker."""
        now_utc = datetime.datetime.now(datetime.UTC)

        # 1. Database Ping
        db_start = time.perf_counter()
        try:
            await db.execute(text("SELECT 1"))
            db_latency = round((time.perf_counter() - db_start) * 1000.0, 2)
            dialect = "postgresql"
            if db.bind and hasattr(db.bind, "dialect") and db.bind.dialect.name:
                dialect = db.bind.dialect.name
            db_health = DatabaseHealth(status="connected", latency_ms=db_latency, dialect=dialect)
        except Exception as e:
            logger.error("Database connection check failed: %s", e)
            db_health = DatabaseHealth(status="disconnected", latency_ms=0.0, dialect="postgresql")

        # 2. Worker & Jobs Health
        processing_res = await db.execute(
            select(func.count(EmailJob.id)).where(EmailJob.status == "processing")
        )
        processing_count = processing_res.scalar_one() or 0

        queued_res = await db.execute(
            select(func.count(EmailJob.id)).where(EmailJob.status == "queued")
        )
        queued_count = queued_res.scalar_one() or 0

        failed_res = await db.execute(
            select(func.count(EmailJob.id)).where(EmailJob.status == "failed")
        )
        failed_count = failed_res.scalar_one() or 0

        latest_job_res = await db.execute(select(func.max(EmailJob.updated_at)))
        latest_job_at = latest_job_res.scalar_one_or_none()

        heartbeat_age = (
            (now_utc - latest_job_at.replace(tzinfo=datetime.UTC if latest_job_at.tzinfo is None else latest_job_at.tzinfo)).total_seconds()
            if latest_job_at
            else None
        )

        worker_status: Any = "alive"
        if heartbeat_age is not None and heartbeat_age > 3600 and (processing_count > 0 or queued_count > 0):
            worker_status = "dead"
        elif heartbeat_age is not None and heartbeat_age > 600 and queued_count > 0:
            worker_status = "stale"

        worker_health = WorkerHealth(
            status=worker_status,
            last_heartbeat_at=latest_job_at,
            heartbeat_age_seconds=round(heartbeat_age, 1) if heartbeat_age else None,
            active_jobs_processing=processing_count,
            queued_jobs_pending=queued_count,
            failed_jobs_count=failed_count,
        )

        # 3. SOCKS5 Proxy Connectivity
        proxy_stmt = (
            select(ProxyProfile)
            .where(ProxyProfile.enabled.is_(True))
            .order_by(ProxyProfile.updated_at.desc())
            .limit(1)
        )
        proxy_profile = (await db.execute(proxy_stmt)).scalar_one_or_none()

        if proxy_profile:
            p_status: Any = "untested"
            if proxy_profile.last_test_status in ("passed", "success"):
                p_status = "passed"
            elif proxy_profile.last_test_status == "failed":
                p_status = "failed"

            proxy_health = ProxyHealth(
                profile_id=proxy_profile.id,
                profile_name=proxy_profile.name,
                protocol=proxy_profile.protocol,
                host_masked=_mask_host(proxy_profile.host, proxy_profile.port),
                status=p_status,
                last_exit_ip=proxy_profile.last_exit_ip,
                last_detected_country=proxy_profile.last_detected_country,
                last_latency_ms=proxy_profile.last_latency_ms,
                last_tested_at=proxy_profile.last_tested_at,
                last_error_code=proxy_profile.last_error_code,
            )
        else:
            proxy_health = ProxyHealth(status="unconfigured")

        # 4. Email Queues & SLA Metrics
        q_ready = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(
                    IncomingEmail.status.in_(["pending_approval", "drafted"]),
                    IncomingEmail.spam_status != "spam",
                )
            )
        ).scalar_one() or 0

        q_manual = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(
                    IncomingEmail.status == "manual_review",
                    IncomingEmail.spam_status != "spam",
                )
            )
        ).scalar_one() or 0

        q_product = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(
                    IncomingEmail.classification_category == "product_inquiry",
                    IncomingEmail.spam_status != "spam",
                )
            )
        ).scalar_one() or 0

        q_order = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(
                    IncomingEmail.customer_status == "has_order_record",
                    IncomingEmail.spam_status != "spam",
                )
            )
        ).scalar_one() or 0

        q_complaint = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(
                    IncomingEmail.classification_category.in_(["complaint", "return_or_refund"]),
                    IncomingEmail.spam_status != "spam",
                )
            )
        ).scalar_one() or 0

        q_spam = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(
                    (IncomingEmail.spam_status == "spam") | (IncomingEmail.status == "spam")
                )
            )
        ).scalar_one() or 0

        q_sent = (
            await db.execute(
                select(func.count(IncomingEmail.id)).where(IncomingEmail.status == "sent")
            )
        ).scalar_one() or 0

        oldest_unreviewed_stmt = (
            select(func.min(IncomingEmail.received_at)).where(
                IncomingEmail.status.in_(["pending_approval", "manual_review", "pending", "drafted", "classified"]),
                IncomingEmail.spam_status != "spam",
                IncomingEmail.status != "spam",
            )
        )
        oldest_received_at = (await db.execute(oldest_unreviewed_stmt)).scalar_one_or_none()

        oldest_age: float | None = None
        if oldest_received_at:
            dt_clean = oldest_received_at.replace(
                tzinfo=datetime.UTC if oldest_received_at.tzinfo is None else oldest_received_at.tzinfo
            )
            oldest_age = (now_utc - dt_clean).total_seconds()

        is_critical = bool((oldest_age is not None and oldest_age > 86400) or q_manual > 50)

        queue_metrics = QueueMetrics(
            ready_to_review=q_ready,
            needs_manual_review=q_manual,
            product_inquiry=q_product,
            recent_order=q_order,
            complaint=q_complaint,
            spam=q_spam,
            sent=q_sent,
            total_unprocessed=q_ready + q_manual,
            oldest_unreviewed_age_seconds=round(oldest_age, 1) if oldest_age else None,
            oldest_unreviewed_received_at=oldest_received_at,
            is_backlog_critical=is_critical,
        )

        # 5. Store Health Matrix
        stores_stmt = (
            select(StoreProfile)
            .options(
                selectinload(StoreProfile.mailboxes),
                selectinload(StoreProfile.shopify_connection),
                selectinload(StoreProfile.policies),
            )
            .order_by(StoreProfile.created_at.asc())
        )
        store_entities = (await db.execute(stores_stmt)).scalars().all()

        store_items: list[StoreHealthItem] = []
        has_degraded_store = False

        for st in store_entities:
            # Mailbox health
            mb_health: MailboxHealth | None = None
            if st.mailboxes:
                mb = st.mailboxes[0]
                cp_stmt = select(MailboxCheckpoint).where(MailboxCheckpoint.mailbox_id == mb.id).limit(1)
                cp = (await db.execute(cp_stmt)).scalar_one_or_none()

                reconcile_age = None
                if cp and cp.last_reconciled_at:
                    cp_reconcile = cp.last_reconciled_at.replace(
                        tzinfo=datetime.UTC if cp.last_reconciled_at.tzinfo is None else cp.last_reconciled_at.tzinfo
                    )
                    reconcile_age = (now_utc - cp_reconcile).total_seconds()

                imap_st: Any = "unconfigured"
                if cp:
                    if cp.state in ("idle", "active", "reconciling", "error"):
                        imap_st = cp.state
                    else:
                        imap_st = "active"
                elif mb.status == "active":
                    imap_st = "active"

                smtp_st: Any = "active" if mb.status == "active" else "error" if mb.status == "error" else "unconfigured"
                if imap_st == "error" or smtp_st == "error":
                    has_degraded_store = True

                mb_health = MailboxHealth(
                    address=mb.address,
                    imap_status=imap_st,
                    idle_connected_at=cp.idle_connected_at if cp else None,
                    idle_heartbeat_at=cp.idle_heartbeat_at if cp else None,
                    last_reconciled_at=cp.last_reconciled_at if cp else None,
                    reconciliation_age_seconds=round(reconcile_age, 1) if reconcile_age else None,
                    smtp_status=smtp_st,
                    last_smtp_success_at=mb.last_smtp_auth_success_at,
                    last_error_code=mb.last_error_code or (cp.last_error_code if cp else None),
                )

            # Shopify connection health
            sh_health: ShopifyConnectionHealth | None = None
            if st.shopify_connection:
                conn = st.shopify_connection
                # Query latest order snapshot lookup timestamp
                snap_stmt = (
                    select(func.max(ShopifyOrderSnapshot.lookup_checked_at))
                    .where(
                        ShopifyOrderSnapshot.store_profile_id == st.id,
                        ShopifyOrderSnapshot.lookup_status.in_(["success", "no_order"]),
                    )
                )
                last_lookup = (await db.execute(snap_stmt)).scalar_one_or_none()
                lookup_age = None
                if last_lookup:
                    snap_time = last_lookup.replace(
                        tzinfo=datetime.UTC if last_lookup.tzinfo is None else last_lookup.tzinfo
                    )
                    lookup_age = (now_utc - snap_time).total_seconds()

                auth_status_val: Any = "unconfigured"
                if conn.auth_status in ("authenticated", "expired", "failed", "unconfigured"):
                    auth_status_val = conn.auth_status

                if auth_status_val == "failed":
                    has_degraded_store = True

                sh_health = ShopifyConnectionHealth(
                    shop_domain=conn.shop_domain,
                    auth_status=auth_status_val,
                    last_successful_lookup_at=last_lookup,
                    last_lookup_age_seconds=round(lookup_age, 1) if lookup_age else None,
                    last_auth_error_code=conn.last_auth_error_code,
                )

            # Policies health
            policy_count = len(st.policies)
            last_policy_sync = max((p.updated_at for p in st.policies), default=None) if st.policies else None
            policy_sync_age = None
            if last_policy_sync:
                pol_time = last_policy_sync.replace(
                    tzinfo=datetime.UTC if last_policy_sync.tzinfo is None else last_policy_sync.tzinfo
                )
                policy_sync_age = (now_utc - pol_time).total_seconds()

            is_policy_stale = bool(
                policy_count == 0
                or (policy_sync_age is not None and policy_sync_age > 7 * 86400)
            )

            pol_health = PoliciesHealth(
                total_policies=policy_count,
                last_synced_at=last_policy_sync,
                sync_age_seconds=round(policy_sync_age, 1) if policy_sync_age else None,
                is_stale=is_policy_stale,
            )

            store_status_val = cast(
                Any,
                st.status
                if st.status in ("active", "paused", "draft", "connection_error", "archived")
                else "active",
            )
            store_items.append(
                StoreHealthItem(
                    store_id=st.id,
                    name=st.name,
                    brand_name=st.brand_name,
                    public_domain=st.public_domain,
                    canonical_domain=st.canonical_domain,
                    status=store_status_val,
                    mailbox=mb_health,
                    shopify=sh_health,
                    policies=pol_health,
                )
            )

        # 6. Overall System Status Determination
        overall_status: Any = "healthy"
        if db_health.status == "disconnected" or worker_health.status == "dead":
            overall_status = "down"
        elif (
            proxy_health.status == "failed"
            or has_degraded_store
            or queue_metrics.is_backlog_critical
            or worker_health.status == "stale"
        ):
            overall_status = "degraded"

        return SystemHealthSummaryResponse(
            status=overall_status,
            timestamp=now_utc,
            uptime_seconds=round(cls.get_uptime_seconds(), 2),
            database=db_health,
            worker=worker_health,
            proxy=proxy_health,
            queues=queue_metrics,
            stores=store_items,
        )

    @classmethod
    async def get_recent_audit_events(
        cls,
        db: AsyncSession,
        limit: int = 10,
        offset: int = 0,
        event_type: str | None = None,
        store_id: uuid.UUID | None = None,
    ) -> AuditEventsResponse:
        """Retrieves paginated audit events safe for operator review without secrets."""
        base_query = select(AuditEvent)
        count_query = select(func.count(AuditEvent.id))

        if event_type:
            base_query = base_query.where(AuditEvent.event_type == event_type)
            count_query = count_query.where(AuditEvent.event_type == event_type)

        if store_id:
            base_query = base_query.where(AuditEvent.store_profile_id == store_id)
            count_query = count_query.where(AuditEvent.store_profile_id == store_id)

        total = (await db.execute(count_query)).scalar_one() or 0

        query = (
            base_query.options(
                selectinload(AuditEvent.actor),
                selectinload(AuditEvent.store_profile),
            )
            .order_by(AuditEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        rows = (await db.execute(query)).scalars().all()
        items: list[AuditEventItem] = []
        for row in rows:
            actor_name = row.actor.username if row.actor else None
            store_name = row.store_profile.name if row.store_profile else None
            items.append(
                AuditEventItem(
                    id=row.id,
                    event_type=row.event_type,
                    actor_user_id=row.actor_user_id,
                    actor_username=actor_name,
                    store_profile_id=row.store_profile_id,
                    store_name=store_name,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    safe_change_summary=row.safe_change_summary,
                    created_at=row.created_at,
                )
            )

        return AuditEventsResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )
