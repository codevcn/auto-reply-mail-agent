"""API Router for System Health, Monitoring, and Audit Events (Phase 7)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.system.schemas import (
    AuditEventsResponse,
    LivenessResponse,
    ReadinessResponse,
    SystemHealthSummaryResponse,
)
from app.system.service import SystemHealthService

system_router = APIRouter(prefix="/system", tags=["System / Observability"])


@system_router.get(
    "/health-summary",
    response_model=SystemHealthSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Comprehensive System Health and Operations Metrics",
)
async def get_health_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SystemHealthSummaryResponse:
    """Returns aggregated real-time operational status for all 4 stores, queues, proxy, and database."""
    return await SystemHealthService.get_health_summary(db)


@system_router.get(
    "/audit",
    response_model=AuditEventsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Recent Audit Events Feed",
)
async def get_recent_audit_events(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    store_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuditEventsResponse:
    """Returns audit trail events with actor and store information, sanitized of secrets."""
    return await SystemHealthService.get_recent_audit_events(
        db=db,
        limit=limit,
        offset=offset,
        event_type=event_type,
        store_id=store_id,
    )


# Probe routes also included in probe router
probes_router = APIRouter(tags=["Health Probes"])


@probes_router.get(
    "/health/live",
    response_model=LivenessResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness Probe",
)
async def liveness_probe() -> LivenessResponse:
    """Non-blocking liveness check for container runtime."""
    return SystemHealthService.check_liveness()


@probes_router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness Probe",
)
async def readiness_probe(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ReadinessResponse:
    """Readiness probe verifying database connectivity before routing traffic."""
    status_code, body = await SystemHealthService.check_readiness(db)
    response.status_code = status_code
    return body
