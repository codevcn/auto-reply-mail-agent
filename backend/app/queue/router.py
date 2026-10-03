"""FastAPI router for Mail Queues and Email Item details."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.db.session import get_db
from app.ingestion.schemas import EmailContentResponse
from app.ingestion.service import MailFetchService
from app.mail.exceptions import SourceMessageUnavailableError
from app.queue.schemas import (
    EmailQueueListResponse,
    OverrideClassificationRequest,
    QueueStatsResponse,
)
from app.queue.service import TransactionalQueueService

queue_router = APIRouter(tags=["Mail Queues"])


@queue_router.get(
    "/queues/stats",
    response_model=QueueStatsResponse,
    dependencies=[Depends(require_permission("mail:read"))],
)
async def get_queue_statistics(
    store_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> QueueStatsResponse:
    """Retrieve item counts across all 7 queues for sidebar badge display."""
    return await TransactionalQueueService.get_queue_stats(db, store_id=store_id)


@queue_router.get(
    "/queues/{queue_type}/emails",
    response_model=EmailQueueListResponse,
    dependencies=[Depends(require_permission("mail:read"))],
)
async def list_queue_emails(
    queue_type: str,
    store_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> EmailQueueListResponse:
    """Retrieve paginated email metadata items for a given queue."""
    return await TransactionalQueueService.get_queue_emails(
        session=db,
        queue_type=queue_type,
        store_id=store_id,
        page=page,
        limit=limit,
        search=search,
    )


@queue_router.get(
    "/emails/{id}",
    response_model=EmailContentResponse,
    dependencies=[Depends(require_permission("mail:read"))],
)
async def get_email_details_with_content(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EmailContentResponse:
    """Retrieve email metadata and on-demand fetch content from mailserver (R-04)."""
    try:
        return await MailFetchService.fetch_email_content(db, id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found.") from exc
    except SourceMessageUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@queue_router.post(
    "/emails/{id}/unmark-spam",
    dependencies=[Depends(require_permission("mail:write"))],
)
async def unmark_email_as_spam(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_permission("mail:write")),
) -> dict[str, str]:
    """INVARIANT R-08: Restores a falsely marked spam email back to active queue."""
    try:
        user_id = current_user.id if hasattr(current_user, "id") else None
        rec = await TransactionalQueueService.unmark_spam(db, id, user_id=user_id)
        return {"status": "ok", "email_id": str(rec.id), "new_status": rec.status}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found.") from exc


@queue_router.post(
    "/emails/{id}/override-classification",
    dependencies=[Depends(require_permission("mail:write"))],
)
async def override_email_classification(
    id: uuid.UUID,
    body: OverrideClassificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_permission("mail:write")),
) -> dict[str, str]:
    """Applies human override to AI classification and optionally enqueues draft."""
    try:
        user_id = current_user.id if hasattr(current_user, "id") else None
        rec = await TransactionalQueueService.override_classification(
            db, id, user_id=user_id, override_data=body
        )
        return {"status": "ok", "email_id": str(rec.id), "new_status": rec.status}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found.") from exc

