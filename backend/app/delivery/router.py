"""API router for Delivery, Human Approval (Invariant R-01), and Send Idempotency (R-26)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models.draft import ReplyDraft, ReplyDraftVersion
from app.db.models.user import User
from app.db.session import get_db
from app.delivery.schemas import (
    ApproveAndSendRequest,
    ApproveAndSendResponse,
    ManualResolveRequest,
)
from app.delivery.service import DeliveryBusinessError, DeliveryService

delivery_router = APIRouter(prefix="", tags=["Delivery & Approval"])


@delivery_router.post(
    "/queue/emails/{email_id}/draft/approve-and-send",
    response_model=ApproveAndSendResponse,
    summary="Approve and send reply draft via SMTP STARTTLS (Zero Autonomous Sending)",
)
@delivery_router.post(
    "/emails/{email_id}/drafts/approve-and-send",
    response_model=ApproveAndSendResponse,
    summary="Alias: Approve and send reply draft",
)
async def approve_and_send_email_draft(
    email_id: uuid.UUID,
    payload: ApproveAndSendRequest | None = None,
    x_idempotency_key: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApproveAndSendResponse:
    # Resolve or generate idempotency key
    key = x_idempotency_key or str(uuid.uuid4())
    version_id = payload.draft_version_id if payload else None
    override_rec = payload.override_recipient if payload else None

    try:
        return await DeliveryService.approve_and_send(
            session=session,
            email_id=email_id,
            idempotency_key=key,
            user_id=current_user.id,
            draft_version_id=version_id,
            override_recipient=override_rec,
        )
    except DeliveryBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@delivery_router.post(
    "/drafts/{target_id}/approve-and-send",
    response_model=ApproveAndSendResponse,
    summary="Alias: Approve and send by draft_id or draft_version_id",
)
async def approve_and_send_by_draft_id(
    target_id: uuid.UUID,
    payload: ApproveAndSendRequest | None = None,
    x_idempotency_key: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApproveAndSendResponse:
    key = x_idempotency_key or str(uuid.uuid4())
    version_id = payload.draft_version_id if payload else None
    override_rec = payload.override_recipient if payload else None

    # First check if target_id is a ReplyDraft
    draft = await session.get(ReplyDraft, target_id)
    if draft:
        resolved_email_id = draft.incoming_email_id
        resolved_version_id = version_id or draft.current_version_id
    else:
        # Check if target_id is a ReplyDraftVersion
        d_ver = await session.get(ReplyDraftVersion, target_id)
        if d_ver:
            resolved_email_id = d_ver.incoming_email_id
            resolved_version_id = d_ver.id
        else:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "DRAFT_NOT_FOUND", "message": "Draft or DraftVersion not found"},
            )

    try:
        return await DeliveryService.approve_and_send(
            session=session,
            email_id=resolved_email_id,
            idempotency_key=key,
            user_id=current_user.id,
            draft_version_id=resolved_version_id,
            override_recipient=override_rec,
        )
    except DeliveryBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@delivery_router.post(
    "/queue/emails/{email_id}/delivery/resolve-sent",
    summary="Manually resolve delivery_unknown status by confirming email was sent",
)
async def resolve_delivery_unknown_sent(
    email_id: uuid.UUID,
    payload: ManualResolveRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        await DeliveryService.resolve_delivery_unknown_sent(
            session=session,
            email_id=email_id,
            notes=payload.notes,
            user_id=current_user.id,
        )
        return {"status": "success", "email_id": str(email_id), "resolved_status": "sent"}
    except DeliveryBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@delivery_router.post(
    "/queue/emails/{email_id}/delivery/resolve-reopen",
    summary="Manually resolve delivery_unknown status by resetting to pending_approval",
)
async def resolve_delivery_unknown_reopen(
    email_id: uuid.UUID,
    payload: ManualResolveRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        await DeliveryService.resolve_delivery_unknown_reopen(
            session=session,
            email_id=email_id,
            notes=payload.notes,
            user_id=current_user.id,
        )
        return {"status": "success", "email_id": str(email_id), "resolved_status": "pending_approval"}
    except DeliveryBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
