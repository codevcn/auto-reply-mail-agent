"""API Router for Data Retention & Cleanup (Invariant R-34)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.retention.schemas import (
    RetentionCleanupReport,
    RetentionRunRequest,
    RetentionStatusResponse,
)
from app.retention.service import RetentionService

retention_router = APIRouter(prefix="/system/retention", tags=["System / Retention"])


@retention_router.post(
    "/run",
    response_model=RetentionCleanupReport,
    status_code=status.HTTP_200_OK,
    summary="Trigger Retention Cleanup Job (Manual or Dry-Run)",
)
async def trigger_retention_cleanup(
    payload: RetentionRunRequest = RetentionRunRequest(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RetentionCleanupReport:
    """Manually triggers 120-day retention cleanup. Supports dry_run mode."""
    report = await RetentionService.run_cleanup_job(
        db=db,
        retention_days=payload.retention_days or 120,
        dry_run=payload.dry_run,
        batch_size=payload.batch_size,
        actor_user_id=current_user.id,
    )
    return report


@retention_router.get(
    "/status",
    response_model=RetentionStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Data Retention Policy Status & Expiry Estimates",
)
async def get_retention_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RetentionStatusResponse:
    """Retrieves current retention configuration, cutoff threshold, and pending counts."""
    return await RetentionService.get_retention_status(db=db)
