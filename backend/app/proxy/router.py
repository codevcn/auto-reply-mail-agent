"""API endpoints for Proxy Profile management and health checks."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.db.models.store import ProxyProfile, StoreProfile
from app.db.session import get_db
from app.proxy.schemas import (
    ProxyCandidateTestRequest,
    ProxyProfileCreate,
    ProxyProfileResponse,
    ProxyProfileUpdate,
    ProxyTestResult,
)
from app.proxy.service import ProxyService

proxy_router = APIRouter(tags=["Proxies"])


def _serialize_proxy_profile(p: ProxyProfile) -> ProxyProfileResponse:
    return ProxyProfileResponse(
        id=p.id,
        name=p.name,
        protocol=p.protocol,
        host=p.host,
        port=p.port,
        username=None,  # Do not leak username
        has_password=bool(p.encrypted_password),
        connect_timeout_seconds=p.connect_timeout_seconds,
        enabled=p.enabled,
        last_test_status=p.last_test_status,
        last_tested_at=p.last_tested_at,
        last_exit_ip=p.last_exit_ip,
        last_detected_country=p.last_detected_country,
        last_latency_ms=p.last_latency_ms,
        last_error_code=p.last_error_code,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@proxy_router.post(
    "/proxies",
    response_model=ProxyProfileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def create_proxy_profile(
    data: ProxyProfileCreate,
    db: AsyncSession = Depends(get_db),
) -> ProxyProfileResponse:
    existing = await ProxyService.get_proxy_profile_by_name(db, data.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proxy profile with name '{data.name}' already exists.",
        )
    profile = await ProxyService.create_proxy_profile(db, data)
    return _serialize_proxy_profile(profile)


@proxy_router.get(
    "/proxies",
    response_model=list[ProxyProfileResponse],
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def list_proxy_profiles(
    db: AsyncSession = Depends(get_db),
) -> list[ProxyProfileResponse]:
    profiles = await ProxyService.list_proxy_profiles(db)
    return [_serialize_proxy_profile(p) for p in profiles]


@proxy_router.get(
    "/proxies/{proxy_id}",
    response_model=ProxyProfileResponse,
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def get_proxy_profile(
    proxy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProxyProfileResponse:
    profile = await ProxyService.get_proxy_profile(db, proxy_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proxy profile not found.")
    return _serialize_proxy_profile(profile)


@proxy_router.put(
    "/proxies/{proxy_id}",
    response_model=ProxyProfileResponse,
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def update_proxy_profile(
    proxy_id: uuid.UUID,
    data: ProxyProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProxyProfileResponse:
    profile = await ProxyService.update_proxy_profile(db, proxy_id, data)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proxy profile not found.")
    return _serialize_proxy_profile(profile)


@proxy_router.delete(
    "/proxies/{proxy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def delete_proxy_profile(
    proxy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    # Check if proxy is attached to any store
    stmt = select(StoreProfile).where(StoreProfile.proxy_profile_id == proxy_id)
    result = await db.execute(stmt)
    store = result.scalars().first()
    if store:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete proxy profile attached to store '{store.name}'.",
        )

    deleted = await ProxyService.delete_proxy_profile(db, proxy_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proxy profile not found.")


@proxy_router.post(
    "/proxies/test",
    response_model=ProxyTestResult,
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def test_candidate_proxy(
    data: ProxyCandidateTestRequest,
) -> ProxyTestResult:
    return await ProxyService.test_candidate_proxy(data)


@proxy_router.post(
    "/proxies/{proxy_id}/test",
    response_model=ProxyTestResult,
    dependencies=[Depends(require_permission("proxies:manage"))],
)
async def test_stored_proxy(
    proxy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProxyTestResult:
    return await ProxyService.test_stored_proxy(db, proxy_id)


@proxy_router.post(
    "/stores/{store_id}/test-proxy",
    response_model=ProxyTestResult,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def test_store_proxy(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProxyTestResult:
    stmt = select(StoreProfile).where(StoreProfile.id == store_id)
    result = await db.execute(stmt)
    store = result.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found.")
    if not store.proxy_profile_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Store has no proxy profile assigned.")
    return await ProxyService.test_stored_proxy(db, store.proxy_profile_id)
