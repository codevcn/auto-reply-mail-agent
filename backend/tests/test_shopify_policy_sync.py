"""Tests for Live Shopify Store Policy Sync, Custom Policies, SHA-256 Hashing, and Two-Tier Caching.

INVARIANTS & SPECIFICATIONS:
- Invariant R-17: 6 Store Policies sync via GraphQL over SOCKS5 proxy (REFUND, PRIVACY,
  TERMS, SHIPPING, CONTACT, LEGAL). Malicious HTML sanitization, clean text normalization,
  standard 64-hex SHA-256 content hashing. Two-tier cache with 300s TTL.
- Invariant R-18: Custom policies support (e.g. WARRANTY, CANCELLATION) with CRUD operations
  and automatic memory cache invalidation.
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock

import pytest
from app.db.models.store import StorePolicy, StoreProfile
from app.shopify.schemas import CustomPolicyCreateRequest, CustomPolicyUpdateRequest
from app.shopify.service import (
    CANONICAL_POLICY_TYPES,
    ShopifyService,
    _memory_policy_cache,
)
from app.store.router import (
    create_custom_policy,
    delete_custom_policy,
    list_custom_policies,
    update_custom_policy,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SAMPLE_GRAPHQL_PRIMARY_POLICIES = {
    "data": {
        "shop": {
            "shopPolicies": [
                {
                    "type": "REFUND_POLICY",
                    "title": "Refund Policy",
                    "body": "<h1>Returns</h1><p>Returns accepted within <strong>30 days</strong>.</p><script>alert('bad')</script>",
                    "url": "https://wrydeco.com/policies/refund-policy",
                },
                {
                    "type": "PRIVACY_POLICY",
                    "title": "Privacy Policy",
                    "body": "<p>We respect your privacy and protect data.</p>",
                    "url": "https://wrydeco.com/policies/privacy-policy",
                },
                {
                    "type": "TERMS_OF_SERVICE",
                    "title": "Terms of Service",
                    "body": "<p>By browsing wrydeco.com you agree to terms.</p>",
                    "url": "https://wrydeco.com/policies/terms-of-service",
                },
                {
                    "type": "SHIPPING_POLICY",
                    "title": "Shipping Policy",
                    "body": "<p>Standard shipping takes 3-5 business days.</p>",
                    "url": "https://wrydeco.com/policies/shipping-policy",
                },
                {
                    "type": "CONTACT_INFORMATION",
                    "title": "Contact Information",
                    "body": "<p>Reach us at support@wrydeco.com</p>",
                    "url": "https://wrydeco.com/policies/contact-information",
                },
                {
                    "type": "LEGAL_NOTICE",
                    "title": "Legal Notice",
                    "body": "<p>Wrydeco Inc. All rights reserved.</p>",
                    "url": "https://wrydeco.com/policies/legal-notice",
                },
            ]
        }
    }
}


@pytest.mark.asyncio
async def test_sync_legal_policies_extracts_and_hashes_all_6_policies() -> None:
    """INVARIANT R-17: All 6 standard policies must have sanitized markdown and valid 64-hex SHA-256."""
    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = SAMPLE_GRAPHQL_PRIMARY_POLICIES

    res = await ShopifyService.sync_legal_policies(client=mock_client)

    assert res.success is True
    assert set(res.policies.keys()) == set(CANONICAL_POLICY_TYPES)
    assert set(res.hashes.keys()) == set(CANONICAL_POLICY_TYPES)

    # 1. Check Refund Policy sanitization
    refund_text = res.policies["REFUND"]
    assert "Returns accepted within 30 days." in refund_text
    assert "<script>" not in refund_text
    assert "alert" not in refund_text

    # 2. Check 64-hex SHA-256 consistency
    for p_type in CANONICAL_POLICY_TYPES:
        text = res.policies[p_type]
        p_hash = res.hashes[p_type]
        assert len(p_hash) == 64
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert p_hash == expected_hash


@pytest.mark.asyncio
async def test_sync_legal_policies_fallback_query_when_primary_empty() -> None:
    """When primary query returns empty shopPolicies, fallback individual fields are read."""
    mock_client = AsyncMock()

    # First call: primary query -> empty list
    # Second call: fallback query -> individual policy fields
    mock_client.execute_graphql.side_effect = [
        {"data": {"shop": {"shopPolicies": []}}},
        {
            "data": {
                "shop": {
                    "refundPolicy": {"body": "<p>Fallback refund policy text</p>"},
                    "privacyPolicy": {"body": "<p>Fallback privacy policy text</p>"},
                    "termsOfService": {"body": "<p>Fallback terms of service text</p>"},
                    "shippingPolicy": {"body": "<p>Fallback shipping policy text</p>"},
                    "contactInformation": {"body": "<p>Fallback contact text</p>"},
                    "legalNotice": {"body": "<p>Fallback legal notice text</p>"},
                }
            }
        },
    ]

    res = await ShopifyService.sync_legal_policies(client=mock_client)

    assert res.success is True
    assert "Fallback refund policy text" in res.policies["REFUND"]
    assert "Fallback shipping policy text" in res.policies["SHIPPING"]
    assert len(res.hashes["REFUND"]) == 64


@pytest.mark.asyncio
async def test_get_effective_store_policies_two_tier_cache(db_session: AsyncSession) -> None:
    """INVARIANT R-17: Two-tier cache test (Tier 1 memory + Tier 2 DB persistent)."""
    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name=f"test-store-policies-{uuid.uuid4().hex[:6]}",
        brand_name="Policy Test Store",
        public_domain="policytest.com",
        status="active",
    )
    db_session.add(store)
    await db_session.commit()

    # Clear memory cache for this store
    _memory_policy_cache.pop(store_id, None)

    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = SAMPLE_GRAPHQL_PRIMARY_POLICIES

    # 1. First call: Cache miss -> calls Shopify -> persists to DB and populates memory cache
    policies_map_1 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
    )

    assert len(policies_map_1) == 6
    assert "REFUND" in policies_map_1
    assert mock_client.execute_graphql.call_count == 1

    # Verify persisted in database
    stmt = select(StorePolicy).where(StorePolicy.store_profile_id == store_id)
    db_records = (await db_session.execute(stmt)).scalars().all()
    assert len(db_records) == 6

    # 2. Second call: Tier 1 Memory Cache Hit -> client should NOT be called again
    mock_client.reset_mock()
    policies_map_2 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
    )

    assert mock_client.execute_graphql.call_count == 0
    assert policies_map_2["REFUND"].content_hash == policies_map_1["REFUND"].content_hash

    # 3. Third call with force_refresh=True -> bypasses Tier 1 and calls client
    policies_map_3 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
        force_refresh=True,
    )

    assert mock_client.execute_graphql.call_count == 1
    assert "REFUND" in policies_map_3


@pytest.mark.asyncio
async def test_custom_policies_crud_and_cache_invalidation(db_session: AsyncSession) -> None:
    """INVARIANT R-18: Custom policy CRUD and automatic memory cache invalidation."""
    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name=f"test-store-custom-pol-{uuid.uuid4().hex[:6]}",
        brand_name="Custom Policy Store",
        public_domain="custompol.com",
        status="active",
    )
    db_session.add(store)
    await db_session.commit()

    # Pre-populate memory cache to test invalidation
    _memory_policy_cache[store_id] = (1000.0, {})

    # 1. Create custom warranty policy
    create_dto = CustomPolicyCreateRequest(
        policy_type="WARRANTY",
        title="Store Warranty Policy",
        content="<p>All electronics come with a <strong>1-year limited warranty</strong>.</p>",
    )

    created_policy = await create_custom_policy(
        store_id=store_id,
        data=create_dto,
        db=db_session,
    )

    assert created_policy.policy_type == "WARRANTY"
    assert created_policy.is_custom is True
    assert "1-year limited warranty" in created_policy.body_text
    assert len(created_policy.content_hash) == 64
    # Check cache invalidation
    assert store_id not in _memory_policy_cache

    # 2. List custom policies
    policies_list = await list_custom_policies(store_id=store_id, db=db_session)
    assert len(policies_list) == 1
    assert policies_list[0].policy_type == "WARRANTY"

    # Pre-populate memory cache again
    _memory_policy_cache[store_id] = (1000.0, {})

    # 3. Update custom policy
    update_dto = CustomPolicyUpdateRequest(
        title="Updated Warranty Policy",
        content="<p>Extended warranty now covers <strong>2 years</strong>.</p>",
    )

    updated_policy = await update_custom_policy(
        store_id=store_id,
        policy_type="WARRANTY",
        data=update_dto,
        db=db_session,
    )

    assert updated_policy.title == "Updated Warranty Policy"
    assert "2 years" in updated_policy.body_text
    assert updated_policy.content_hash != created_policy.content_hash
    # Check cache invalidation
    assert store_id not in _memory_policy_cache

    # 4. Delete custom policy
    _memory_policy_cache[store_id] = (1000.0, {})

    delete_res = await delete_custom_policy(
        store_id=store_id,
        policy_type="WARRANTY",
        db=db_session,
    )

    assert delete_res["success"] is True
    assert store_id not in _memory_policy_cache

    # Verify no custom policies remain in DB
    remaining = await list_custom_policies(store_id=store_id, db=db_session)
    assert len(remaining) == 0
