"""API endpoints for Shopify integration, connection testing, policy sync, product search, and order lookup."""

from __future__ import annotations

import contextlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.crypto import decrypt_secret
from app.db.models.store import ShopifyConnection, StoreProfile
from app.db.session import get_db
from app.proxy.client import ResolvedProxyConfig
from app.proxy.service import ProxyService
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.schemas import (
    ProductSearchSnapshotData,
    ShopifyCandidateTestRequest,
    ShopifyConnectionTestResult,
    ShopifyOrderLookupResult,
    ShopifyPoliciesSyncResult,
    StorePolicyItem,
)
from app.shopify.service import ShopifyService
from app.shopify.token import ShopifyTokenManager

shopify_router = APIRouter(tags=["Shopify"])


class OrderLookupRequest(BaseModel):
    email: str = Field(..., description="Customer email to look up")
    days: int = Field(default=60, ge=1, le=180)


class ProductSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Product keywords to search")
    first: int = Field(default=5, ge=1, le=10)


async def _resolve_shopify_client_for_store(
    store_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[StoreProfile, ProxyEnforcedShopifyClient]:
    """Helper to build a ProxyEnforcedShopifyClient for a given store profile."""
    stmt = select(StoreProfile).where(StoreProfile.id == store_id)
    res = await db.execute(stmt)
    store = res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found.")

    conn_stmt = select(ShopifyConnection).where(ShopifyConnection.store_profile_id == store_id)
    conn_res = await db.execute(conn_stmt)
    conn = conn_res.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Shopify connection not configured.")

    proxy_id = conn.proxy_profile_id or store.proxy_profile_id
    if not proxy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fail-closed invariant: SOCKS5 proxy required for Shopify access.",
        )

    proxy_profile = await ProxyService.get_proxy_profile(db, proxy_id)
    if not proxy_profile:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assigned proxy profile not found.")

    proxy_config = ProxyService.resolve_proxy_config(proxy_profile)
    client_secret = decrypt_secret(conn.encrypted_client_secret)

    token_mgr = ShopifyTokenManager(
        shop_domain=conn.shop_domain,
        client_id=conn.client_id,
        client_secret=client_secret,
        proxy_config=proxy_config,
    )
    client = ProxyEnforcedShopifyClient(
        shop_domain=conn.shop_domain,
        token_manager=token_mgr,
        proxy_config=proxy_config,
    )
    return store, client


@shopify_router.post(
    "/shopify/test",
    response_model=ShopifyConnectionTestResult,
    dependencies=[Depends(require_permission("shopify:manage"))],
)
async def test_candidate_shopify(
    data: ShopifyCandidateTestRequest,
    db: AsyncSession = Depends(get_db),
) -> ShopifyConnectionTestResult:
    # 1. Resolve proxy configuration
    if data.proxy_profile_id:
        try:
            proxy_uuid = uuid.UUID(data.proxy_profile_id)
            proxy_profile = await ProxyService.get_proxy_profile(db, proxy_uuid)
            if not proxy_profile:
                return ShopifyConnectionTestResult(
                    success=False,
                    error="PROXY_NOT_FOUND",
                    detail=f"Proxy profile {data.proxy_profile_id} not found.",
                )
            proxy_config = ProxyService.resolve_proxy_config(proxy_profile)
        except ValueError:
            return ShopifyConnectionTestResult(
                success=False,
                error="INVALID_PROXY_ID",
                detail="Invalid UUID format for proxy_profile_id.",
            )
    elif data.proxy_host and data.proxy_port:
        proxy_config = ResolvedProxyConfig(
            host=data.proxy_host,
            port=data.proxy_port,
            username=data.proxy_username,
            password=data.proxy_password,
            protocol="socks5",
        )
    else:
        return ShopifyConnectionTestResult(
            success=False,
            error="PROXY_CONFIG_REQUIRED",
            detail="Fail-closed invariant enforced: SOCKS5 proxy configuration is required.",
        )

    return await ShopifyService.test_connection(
        shop_domain=data.shop_domain,
        client_id=data.client_id,
        client_secret=data.client_secret,
        proxy_config=proxy_config,
    )


@shopify_router.post(
    "/stores/{store_id}/test-shopify",
    response_model=ShopifyConnectionTestResult,
    dependencies=[Depends(require_permission("stores:write"))],
)
async def test_store_shopify(
    store_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ShopifyConnectionTestResult:
    stmt = select(StoreProfile).where(StoreProfile.id == store_id)
    result = await db.execute(stmt)
    store = result.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store profile not found.")

    conn_stmt = select(ShopifyConnection).where(ShopifyConnection.store_profile_id == store_id)
    conn_res = await db.execute(conn_stmt)
    conn = conn_res.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Store has no Shopify connection configured.")

    proxy_id = conn.proxy_profile_id or store.proxy_profile_id
    if not proxy_id:
        return ShopifyConnectionTestResult(
            success=False,
            error="PROXY_CONFIG_REQUIRED",
            detail="Fail-closed invariant enforced: Store has no SOCKS5 proxy assigned.",
        )

    proxy_profile = await ProxyService.get_proxy_profile(db, proxy_id)
    if not proxy_profile:
        return ShopifyConnectionTestResult(
            success=False,
            error="PROXY_NOT_FOUND",
            detail="Assigned proxy profile not found.",
        )

    proxy_config = ProxyService.resolve_proxy_config(proxy_profile)
    client_secret = decrypt_secret(conn.encrypted_client_secret)

    test_res = await ShopifyService.test_connection(
        shop_domain=conn.shop_domain,
        client_id=conn.client_id,
        client_secret=client_secret,
        proxy_config=proxy_config,
    )

    if test_res.success:
        conn.auth_status = "authenticated"
        conn.last_auth_error_code = None
    else:
        conn.auth_status = "failed"
        conn.last_auth_error_code = test_res.error
    await db.commit()

    return test_res


@shopify_router.post(
    "/stores/{store_id}/sync-policies",
    response_model=ShopifyPoliciesSyncResult,
    dependencies=[Depends(require_permission("shopify:manage"))],
)
async def sync_store_policies(
    store_id: uuid.UUID,
    force_refresh: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> ShopifyPoliciesSyncResult:
    """Syncs 6 legal policies from Shopify GraphQL via SOCKS5 proxy and persists to database."""
    _, client = await _resolve_shopify_client_for_store(store_id, db)
    sync_res = await ShopifyService.sync_legal_policies(client)
    if sync_res.success:
        # Also persist through get_effective_store_policies
        await ShopifyService.get_effective_store_policies(
            session=db,
            store_profile_id=store_id,
            client=client,
            force_refresh=force_refresh,
        )
    return sync_res


@shopify_router.get(
    "/stores/{store_id}/policies",
    response_model=list[StorePolicyItem],
    dependencies=[Depends(require_permission("stores:read"))],
)
async def get_store_policies(
    store_id: uuid.UUID,
    force_refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[StorePolicyItem]:
    """Returns store policies (both Shopify synced and custom) utilizing two-tier cache (TTL 300s)."""
    client: ProxyEnforcedShopifyClient | None = None
    with contextlib.suppress(Exception):
        _, client = await _resolve_shopify_client_for_store(store_id, db)

    policies_map = await ShopifyService.get_effective_store_policies(
        session=db,
        store_profile_id=store_id,
        client=client,
        force_refresh=force_refresh,
    )
    return list(policies_map.values())


@shopify_router.post(
    "/stores/{store_id}/orders/lookup",
    response_model=ShopifyOrderLookupResult,
    dependencies=[Depends(require_permission("shopify:manage"))],
)
async def lookup_customer_orders(
    store_id: uuid.UUID,
    payload: OrderLookupRequest,
    db: AsyncSession = Depends(get_db),
) -> ShopifyOrderLookupResult:
    """INVARIANT R-09, R-10, R-11: Looks up orders within 60 days for customer email via SOCKS5 proxy."""
    _, client = await _resolve_shopify_client_for_store(store_id, db)
    return await ShopifyService.enrich_order_lookup(
        client=client,
        customer_email=payload.email,
        days=payload.days,
    )


@shopify_router.post(
    "/stores/{store_id}/products/search",
    response_model=ProductSearchSnapshotData,
    dependencies=[Depends(require_permission("shopify:manage"))],
)
async def search_store_products(
    store_id: uuid.UUID,
    payload: ProductSearchRequest,
    db: AsyncSession = Depends(get_db),
) -> ProductSearchSnapshotData:
    """INVARIANT R-16: Live product search via SOCKS5 proxy without catalog mirroring."""
    _, client = await _resolve_shopify_client_for_store(store_id, db)
    terms = [t.strip() for t in payload.query.split() if t.strip()]
    return await ShopifyService.search_products_live(
        client=client,
        search_terms=terms,
        max_results=payload.first,
    )
