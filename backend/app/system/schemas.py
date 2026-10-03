"""Pydantic schemas for System Health, Observability, and Audit Trail (Phase 7)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class LivenessResponse(BaseModel):
    """Container liveness probe response."""

    status: str = "alive"
    timestamp: datetime.datetime
    uptime_seconds: float


class DatabaseHealth(BaseModel):
    """Database connectivity and latency metrics."""

    status: Literal["connected", "disconnected"]
    latency_ms: float
    dialect: str = "postgresql"


class ReadinessResponse(BaseModel):
    """Readiness probe response for orchestrator and reverse proxy routing."""

    status: Literal["ready", "unready"]
    database: DatabaseHealth
    timestamp: datetime.datetime


class WorkerHealth(BaseModel):
    """Background worker process heartbeat and job counts."""

    status: Literal["alive", "stale", "dead"]
    last_heartbeat_at: datetime.datetime | None
    heartbeat_age_seconds: float | None
    active_jobs_processing: int
    queued_jobs_pending: int
    failed_jobs_count: int


class ProxyHealth(BaseModel):
    """SOCKS5 Proxy connectivity and exit IP metrics."""

    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    protocol: str = "socks5"
    host_masked: str | None = None
    status: Literal["passed", "failed", "unconfigured", "untested"] = "unconfigured"
    last_exit_ip: str | None = None
    last_detected_country: str | None = None
    last_latency_ms: int | None = None
    last_tested_at: datetime.datetime | None = None
    last_error_code: str | None = None


class MailboxHealth(BaseModel):
    """Per-store IMAP IDLE and SMTP connectivity status."""

    address: str
    imap_status: Literal["idle", "active", "reconciling", "error", "unconfigured"]
    idle_connected_at: datetime.datetime | None = None
    idle_heartbeat_at: datetime.datetime | None = None
    last_reconciled_at: datetime.datetime | None = None
    reconciliation_age_seconds: float | None = None
    smtp_status: Literal["active", "error", "unconfigured"] = "active"
    last_smtp_success_at: datetime.datetime | None = None
    last_error_code: str | None = None


class ShopifyConnectionHealth(BaseModel):
    """Shopify connection authentication and recent lookup stats."""

    shop_domain: str
    auth_status: Literal["authenticated", "expired", "failed", "unconfigured"]
    last_successful_lookup_at: datetime.datetime | None = None
    last_lookup_age_seconds: float | None = None
    last_auth_error_code: str | None = None


class PoliciesHealth(BaseModel):
    """Shopify policy synchronization metrics and cache freshness."""

    total_policies: int = 0
    last_synced_at: datetime.datetime | None = None
    sync_age_seconds: float | None = None
    is_stale: bool = False


class StoreHealthItem(BaseModel):
    """Health item for one managed Shopify store."""

    store_id: uuid.UUID
    name: str
    brand_name: str
    public_domain: str
    canonical_domain: str | None = None
    status: Literal["active", "paused", "draft", "connection_error", "archived"]
    mailbox: MailboxHealth | None = None
    shopify: ShopifyConnectionHealth | None = None
    policies: PoliciesHealth = Field(default_factory=PoliciesHealth)


class QueueMetrics(BaseModel):
    """Real-time metrics for 7 email queues and backlog SLA."""

    ready_to_review: int = 0
    needs_manual_review: int = 0
    product_inquiry: int = 0
    recent_order: int = 0
    complaint: int = 0
    spam: int = 0
    sent: int = 0
    total_unprocessed: int = 0
    oldest_unreviewed_age_seconds: float | None = None
    oldest_unreviewed_received_at: datetime.datetime | None = None
    is_backlog_critical: bool = False


class SystemHealthSummaryResponse(BaseModel):
    """Comprehensive system health and operations summary."""

    status: Literal["healthy", "degraded", "down"]
    timestamp: datetime.datetime
    uptime_seconds: float
    database: DatabaseHealth
    worker: WorkerHealth
    proxy: ProxyHealth
    queues: QueueMetrics
    stores: list[StoreHealthItem]


class AuditEventItem(BaseModel):
    """Single audit event item safe for dashboard display."""

    id: uuid.UUID
    event_type: str
    actor_user_id: uuid.UUID | None = None
    actor_username: str | None = None
    store_profile_id: uuid.UUID | None = None
    store_name: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    safe_change_summary: dict[str, Any] | None = None
    created_at: datetime.datetime


class AuditEventsResponse(BaseModel):
    """Paginated list of system audit events."""

    items: list[AuditEventItem]
    total: int
    limit: int
    offset: int
