"""API endpoints for Store Profile management and Setup Wizard."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.db.models.store import StorePolicy, StoreProfile
from app.db.models.user import User
from app.db.session import get_db
from app.shopify.cleaner import compute_content_hash, normalize_policy_text, sanitize_policy_html
from app.shopify.schemas import (
    CustomPolicyCreateRequest,
    CustomPolicyUpdateRequest,
    StorePolicyItem,
)
from app.shopify.service import _memory_policy_cache
from app.store.schemas import (
    StoreActivateRequest,
    StoreActivateResponse,
    StoreProfileCreate,
    StoreProfileResponse,
    StoreProfileUpdate,
)
from app.store.services import StoreWizardService

store_router = APIRouter(tags=["Stores"])


def _serialize_store_profile(s: StoreProfile) -> StoreProfileResponse:
    return StoreProfileResponse(
        id=s.id,
        name=s.name,
        brand_name=s.brand_name,
        public_domain=s.public_domain,
        canonical_domain=s.canonical_domain,
        industry=s.industry,
        brand_description=s.brand_description,
        default_language=s.default_language,
        tone_of_voice=s.tone_of_voice,
        email_signature=s.email_signature,
        status=s.status,
        proxy_profile_id=s.proxy_profile_id,
        activation_baseline_uid=s.activation_baseline_uid,
        uid_validity=s.uid_validity,
        has_mailbox=bool(s.mailboxes),
        has_shopify=bool(s.shopify_connection),
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@store_router.post(
    "/stores",
    response_model=StoreProfileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def create_store(
    data: StoreProfileCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoreProfileResponse:
    try:
        store = await StoreWizardService.create_store_profile(db, data, created_by=current_user.id)
        # Re-fetch with relationships loaded
        loaded_store = await StoreWizardService.get_store_profile(db, store.id)
        assert loaded_store is not None
        return _serialize_store_profile(loaded_store)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@store_router.get(
    "/stores",
    response_model=list[StoreProfileResponse],
    dependencies=[Depends(require_permission("stores:read"))],
)
async def list_stores(
    db: AsyncSession = Depends(get_db),
) -> list[StoreProfileResponse]:
    stores = await StoreWizardService.list_store_profiles(db)
    return [_serialize_store_profile(s) for s in stores]


@store_router.get(
    "/stores/{store_id}",
    response_model=StoreProfileResponse,
    dependencies=[Depends(require_permission("stores:read"))],
)
async def get_store(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StoreProfileResponse:
    store = await StoreWizardService.get_store_profile(db, store_id)
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found.")
    return _serialize_store_profile(store)


@store_router.put(
    "/stores/{store_id}",
    response_model=StoreProfileResponse,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def update_store(
    store_id: uuid.UUID,
    data: StoreProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> StoreProfileResponse:
    try:
        store = await StoreWizardService.update_store_profile(db, store_id, data)
        if not store:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found.")
        return _serialize_store_profile(store)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@store_router.post(
    "/stores/{store_id}/activate",
    response_model=StoreActivateResponse,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def activate_store(
    store_id: uuid.UUID,
    data: StoreActivateRequest,
    db: AsyncSession = Depends(get_db),
) -> StoreActivateResponse:
    ok, msg = await StoreWizardService.activate_profile(
        db=db,
        store_id=store_id,
        mailbox_tested=data.mailbox_tested,
        proxy_tested=data.proxy_tested,
        shopify_tested=data.shopify_tested,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg,
        )
    return StoreActivateResponse(
        success=True,
        status="active",
        message="Store profile activated",
        store_id=store_id,
    )


# --- Custom Policies Management (Invariant R-18) ---
@store_router.get(
    "/stores/{store_id}/policies/custom",
    response_model=list[StorePolicyItem],
    dependencies=[Depends(require_permission("stores:read"))],
)
async def list_custom_policies(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[StorePolicyItem]:
    stmt = (
        select(StorePolicy)
        .where(StorePolicy.store_profile_id == store_id, StorePolicy.is_custom.is_(True))
        .order_by(StorePolicy.policy_type.asc())
    )
    records = (await db.execute(stmt)).scalars().all()
    return [
        StorePolicyItem(
            id=str(r.id),
            store_profile_id=str(r.store_profile_id),
            policy_type=r.policy_type,
            title=r.title,
            body_text=r.body_text,
            body_html=r.body_html,
            content_hash=r.content_hash,
            url=r.url,
            is_custom=r.is_custom,
            synced_at=r.synced_at,
        )
        for r in records
    ]


@store_router.post(
    "/stores/{store_id}/policies/custom",
    response_model=StorePolicyItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def create_custom_policy(
    store_id: uuid.UUID,
    data: CustomPolicyCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> StorePolicyItem:
    store = await StoreWizardService.get_store_profile(db, store_id)
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found.")

    p_type = data.policy_type.strip().upper()
    # Check duplicate
    stmt = select(StorePolicy).where(
        StorePolicy.store_profile_id == store_id,
        StorePolicy.policy_type == p_type,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Policy of type '{p_type}' already exists for this store.",
        )

    clean_text = normalize_policy_text(data.content)
    clean_html = sanitize_policy_html(data.content)
    p_hash = compute_content_hash(clean_text)

    policy = StorePolicy(
        id=uuid.uuid4(),
        store_profile_id=store_id,
        policy_type=p_type,
        title=data.title.strip(),
        body_text=clean_text,
        body_html=clean_html,
        content_hash=p_hash,
        is_custom=True,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    # Invalidate cache
    _memory_policy_cache.pop(store_id, None)

    return StorePolicyItem(
        id=str(policy.id),
        store_profile_id=str(policy.store_profile_id),
        policy_type=policy.policy_type,
        title=policy.title,
        body_text=policy.body_text,
        body_html=policy.body_html,
        content_hash=policy.content_hash,
        url=policy.url,
        is_custom=policy.is_custom,
        synced_at=policy.synced_at,
    )


@store_router.put(
    "/stores/{store_id}/policies/custom/{policy_type}",
    response_model=StorePolicyItem,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def update_custom_policy(
    store_id: uuid.UUID,
    policy_type: str,
    data: CustomPolicyUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> StorePolicyItem:
    p_type = policy_type.strip().upper()
    stmt = select(StorePolicy).where(
        StorePolicy.store_profile_id == store_id,
        StorePolicy.policy_type == p_type,
        StorePolicy.is_custom.is_(True),
    )
    policy = (await db.execute(stmt)).scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom policy not found.")

    clean_text = normalize_policy_text(data.content)
    clean_html = sanitize_policy_html(data.content)
    p_hash = compute_content_hash(clean_text)

    policy.title = data.title.strip()
    policy.body_text = clean_text
    policy.body_html = clean_html
    policy.content_hash = p_hash
    policy.row_version += 1

    await db.commit()
    await db.refresh(policy)

    # Invalidate cache
    _memory_policy_cache.pop(store_id, None)

    return StorePolicyItem(
        id=str(policy.id),
        store_profile_id=str(policy.store_profile_id),
        policy_type=policy.policy_type,
        title=policy.title,
        body_text=policy.body_text,
        body_html=policy.body_html,
        content_hash=policy.content_hash,
        url=policy.url,
        is_custom=policy.is_custom,
        synced_at=policy.synced_at,
    )


@store_router.delete(
    "/stores/{store_id}/policies/custom/{policy_type}",
    dependencies=[Depends(require_permission("stores:write"))],
)
async def delete_custom_policy(
    store_id: uuid.UUID,
    policy_type: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    p_type = policy_type.strip().upper()
    stmt = select(StorePolicy).where(
        StorePolicy.store_profile_id == store_id,
        StorePolicy.policy_type == p_type,
        StorePolicy.is_custom.is_(True),
    )
    policy = (await db.execute(stmt)).scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom policy not found.")

    await db.execute(
        delete(StorePolicy).where(
            StorePolicy.store_profile_id == store_id,
            StorePolicy.policy_type == p_type,
            StorePolicy.is_custom.is_(True),
        )
    )
    await db.commit()

    # Invalidate cache
    _memory_policy_cache.pop(store_id, None)

    return {"success": True, "deleted_policy_type": p_type}
