"""API router for Reply Drafts, Immutable Versioning, and Regeneration."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models.draft import ReplyDraft
from app.db.models.user import User
from app.db.session import get_db
from app.draft.schemas import (
    CreateDraftVersionRequest,
    DraftVersionDTO,
    RegenerateDraftRequest,
    RejectDraftRequest,
    ReplyDraftDetailResponse,
)
from app.draft.service import DraftBusinessError, DraftService

draft_router = APIRouter(prefix="", tags=["Reply Drafts"])


@draft_router.get(
    "/drafts/email/{email_id}",
    response_model=ReplyDraftDetailResponse,
    summary="Get reply draft detail with version history for an email",
)
@draft_router.get(
    "/queue/emails/{email_id}/draft",
    response_model=ReplyDraftDetailResponse,
    summary="Alias: Get reply draft detail for an email",
)
async def get_email_draft(
    email_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ReplyDraftDetailResponse:
    try:
        return await DraftService.get_draft_detail(session, email_id)
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@draft_router.post(
    "/drafts/email/{email_id}/generate",
    response_model=ReplyDraftDetailResponse,
    summary="Generate initial AI draft for an email",
)
async def generate_initial_draft_endpoint(
    email_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ReplyDraftDetailResponse:
    try:
        await DraftService.generate_initial_draft(session, email_id)
        return await DraftService.get_draft_detail(session, email_id)
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@draft_router.post(
    "/drafts/email/{email_id}/version",
    response_model=DraftVersionDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Save a manual edit as a new immutable draft version",
)
@draft_router.post(
    "/emails/{email_id}/drafts",
    response_model=DraftVersionDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Alias: Save a manual edit as a new immutable draft version",
)
async def create_user_draft_version(
    email_id: uuid.UUID,
    payload: CreateDraftVersionRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DraftVersionDTO:
    try:
        version = await DraftService.save_user_version(
            session=session,
            email_id=email_id,
            subject=payload.subject,
            body_text=payload.body_text,
            body_html=payload.body_html,
            user_id=current_user.id,
        )
        return DraftVersionDTO.model_validate(version)
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@draft_router.put(
    "/drafts/{draft_id}",
    response_model=DraftVersionDTO,
    summary="Alias: Update draft (creates a new immutable version)",
)
async def update_draft_put(
    draft_id: uuid.UUID,
    payload: CreateDraftVersionRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DraftVersionDTO:
    draft = await session.get(ReplyDraft, draft_id)
    if not draft:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "DRAFT_NOT_FOUND", "message": "Draft not found"},
        )
    try:
        version = await DraftService.save_user_version(
            session=session,
            email_id=draft.incoming_email_id,
            subject=payload.subject,
            body_text=payload.body_text,
            body_html=payload.body_html,
            user_id=current_user.id,
        )
        return DraftVersionDTO.model_validate(version)
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@draft_router.post(
    "/drafts/email/{email_id}/regenerate",
    response_model=DraftVersionDTO,
    summary="Regenerate reply draft with AI using specified language or policies",
)
@draft_router.post(
    "/emails/{email_id}/drafts/regenerate",
    response_model=DraftVersionDTO,
    summary="Alias: Regenerate reply draft with AI",
)
async def regenerate_email_draft(
    email_id: uuid.UUID,
    payload: RegenerateDraftRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DraftVersionDTO:
    try:
        version = await DraftService.regenerate_draft(
            session=session,
            email_id=email_id,
            target_language=payload.target_language,
            custom_instructions=payload.custom_instructions,
            user_id=current_user.id,
        )
        return DraftVersionDTO.model_validate(version)
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@draft_router.post(
    "/drafts/{draft_id}/regenerate",
    response_model=DraftVersionDTO,
    summary="Alias: Regenerate reply draft by draft_id",
)
async def regenerate_by_draft_id(
    draft_id: uuid.UUID,
    payload: RegenerateDraftRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DraftVersionDTO:
    draft = await session.get(ReplyDraft, draft_id)
    if not draft:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "DRAFT_NOT_FOUND", "message": "Draft not found"},
        )
    try:
        version = await DraftService.regenerate_draft(
            session=session,
            email_id=draft.incoming_email_id,
            target_language=payload.target_language,
            custom_instructions=payload.custom_instructions,
            user_id=current_user.id,
        )
        return DraftVersionDTO.model_validate(version)
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@draft_router.post(
    "/drafts/email/{email_id}/reject",
    summary="Reject draft or mark email as no reply needed",
)
@draft_router.post(
    "/emails/{email_id}/mark-no-reply",
    summary="Alias: Mark email as no reply needed",
)
async def reject_email_draft(
    email_id: uuid.UUID,
    payload: RejectDraftRequest | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    reason = payload.reason if payload else None
    action_type = payload.action_type if payload else "no_reply_needed"
    try:
        await DraftService.reject_draft(
            session=session,
            email_id=email_id,
            reason=reason,
            action_type=action_type,
            user_id=current_user.id,
        )
        return {"status": "success", "email_id": str(email_id), "action": action_type}
    except DraftBusinessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
