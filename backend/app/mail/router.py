"""FastAPI endpoints for Mailbox testing and health checks."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.db.session import get_db
from app.mail.exceptions import PiezaprintExclusionError
from app.mail.schemas import (
    MailboxCandidateTestRequest,
    MailboxConnectionTestResponse,
)
from app.mail.service import MailService

mail_router = APIRouter(tags=["Mailbox"])


@mail_router.post(
    "/mail/test-connection",
    response_model=MailboxConnectionTestResponse,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def test_candidate_mail_connection(
    data: MailboxCandidateTestRequest,
) -> MailboxConnectionTestResponse:
    """Test candidate mailbox credentials without persisting to database."""
    try:
        return await MailService.test_candidate_mailbox(data)
    except PiezaprintExclusionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@mail_router.post(
    "/stores/{store_id}/test-mailbox",
    response_model=MailboxConnectionTestResponse,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def test_store_mailbox_connection(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> MailboxConnectionTestResponse:
    """Test stored mailbox credentials for a configured Store Profile."""
    try:
        return await MailService.test_store_mailbox(db, store_id)
    except PiezaprintExclusionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    except ValueError as exc:
        err_msg = str(exc)
        if err_msg == "STORE_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found."
            ) from exc
        if err_msg == "MAILBOX_NOT_CONFIGURED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Store profile has no mailbox configured.",
            ) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg) from exc
