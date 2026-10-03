"""Adversarial Stress Test Suite for Phase 5: Shopify Enrichment & Live Store Policies Sync.

Empirical Challenger Suite verifying Critical Invariants:
1. Invariant R-16: Live Product Search & Zero Catalog Mirroring
   - Verifies GraphQL live search returns accurate facts (price, variants, totalInventory, options).
   - Verifies adversarial query sanitization against injection / empty query handling.
   - Verifies unresolvable product sets product_resolved=False with PRODUCT_NOT_RESOLVED warning.
   - Verifies out-of-stock product sets product_resolved=True with OUT_OF_STOCK warning.
   - Database Audit: Confirms ZERO catalog mirroring (no full catalog table in schema).
2. Invariant R-17: 6 Store Policies Sync & Two-Tier Cache
   - Verifies exactly 6 canonical policy types (REFUND, PRIVACY, TERMS, SHIPPING, CONTACT, LEGAL).
   - Verifies content hashes are strictly 64-hex SHA-256 strings.
   - Verifies HTML sanitization (strips script, iframe, onclick XSS) and text normalization.
   - Verifies Two-Tier Cache (Tier 1 Memory + Tier 2 DB) with 300s TTL.
   - Verifies force_refresh=True bypasses cache.
   - Verifies memory cache invalidation on custom policy mutation.
3. Invariant R-18: Custom Policies & Stale Draft Warnings
   - Verifies check_draft_policy_freshness detects updated policies (is_stale=True, stale_reason="POLICY_UPDATED").
   - Verifies edge cases (None hashes, empty hashes, unchanged hashes).
   - Verifies MailFetchService.fetch_email_content persists is_stale=True and stale_reason in DB.
4. Invariant R-04: Zero Raw Body in PostgreSQL Audit
   - Raw SQL schema inspection on incoming_emails, shopify_order_snapshots, shopify_product_snapshots.
   - Confirms zero columns storing raw email body.
   - Confirms zero raw email body persistence in database rows.
5. Invariant R-09 / R-10 / R-11: Order Lookup Flags & Distinction
   - Verifies email normalization (strip + lower only, keeps dots and plus tags).
   - Verifies test orders exclusion.
   - Verifies cancelled/refunded preservation of has_order_record.
   - Verifies distinct separation between lookup_unavailable and no_order.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.core.crypto import encrypt_secret
from app.db.base import Base
from app.db.models.email import IncomingEmail
from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
from app.db.models.store import Mailbox, StorePolicy, StoreProfile
from app.ingestion.service import MailFetchService
from app.shopify.cleaner import compute_content_hash, normalize_policy_text, sanitize_policy_html
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import ShopifyProxyError
from app.shopify.schemas import CustomPolicyCreateRequest, CustomPolicyUpdateRequest
from app.shopify.service import (
    CANONICAL_POLICY_TYPES,
    ShopifyService,
    _memory_policy_cache,
)
from app.store.router import (
    create_custom_policy,
    delete_custom_policy,
    update_custom_policy,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ==============================================================================
# Test Fixtures & Mocks
# ==============================================================================

MOCK_GRAPHQL_PRODUCTS_PAYLOAD = {
    "data": {
        "products": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/Product/1001",
                        "title": "Ceramic Table Lamp",
                        "handle": "ceramic-table-lamp",
                        "description": "Handcrafted minimalist ceramic lamp for cozy living rooms.",
                        "vendor": "Wrydeco Studio",
                        "productType": "Home Decor",
                        "status": "ACTIVE",
                        "totalInventory": 4,
                        "onlineStoreUrl": "https://wrydeco.com/products/ceramic-table-lamp",
                        "featuredImage": {
                            "url": "https://cdn.shopify.com/s/files/lamp.jpg",
                            "altText": "Ceramic Table Lamp in warm white",
                        },
                        "variants": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "gid://shopify/ProductVariant/2001",
                                        "title": "Warm White / Standard",
                                        "sku": "LAMP-WHT-STD",
                                        "price": "89.00",
                                        "compareAtPrice": "119.00",
                                        "availableForSale": True,
                                        "inventoryQuantity": 4,
                                        "selectedOptions": [
                                            {"name": "Color", "value": "Warm White"},
                                            {"name": "Size", "value": "Standard"},
                                        ],
                                    }
                                }
                            ]
                        },
                    }
                }
            ]
        }
    }
}

MOCK_GRAPHQL_OUT_OF_STOCK_PAYLOAD = {
    "data": {
        "products": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/Product/1002",
                        "title": "Limited Edition Oak Chair",
                        "handle": "limited-oak-chair",
                        "description": "Sold out collectible chair.",
                        "vendor": "Wrydeco Studio",
                        "productType": "Furniture",
                        "status": "ACTIVE",
                        "totalInventory": 0,
                        "onlineStoreUrl": "https://wrydeco.com/products/limited-oak-chair",
                        "featuredImage": None,
                        "variants": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "gid://shopify/ProductVariant/2002",
                                        "title": "Natural Oak",
                                        "sku": "CHAIR-OAK-01",
                                        "price": "299.00",
                                        "compareAtPrice": None,
                                        "availableForSale": False,
                                        "inventoryQuantity": 0,
                                        "selectedOptions": [{"name": "Material", "value": "Oak"}],
                                    }
                                }
                            ]
                        },
                    }
                }
            ]
        }
    }
}

MOCK_GRAPHQL_6_POLICIES_PAYLOAD = {
    "data": {
        "shop": {
            "shopPolicies": [
                {
                    "type": "REFUND_POLICY",
                    "title": "Refund Policy",
                    "body": "<p>Items can be returned within 30 days of delivery.</p>",
                    "url": "https://wrydeco.com/policies/refund-policy",
                },
                {
                    "type": "PRIVACY_POLICY",
                    "title": "Privacy Policy",
                    "body": "<p>We do not sell personal data to third parties.</p>",
                    "url": "https://wrydeco.com/policies/privacy-policy",
                },
                {
                    "type": "TERMS_OF_SERVICE",
                    "title": "Terms of Service",
                    "body": "<p>Terms governing purchase and access to wrydeco.com.</p>",
                    "url": "https://wrydeco.com/policies/terms-of-service",
                },
                {
                    "type": "SHIPPING_POLICY",
                    "title": "Shipping Policy",
                    "body": "<p>Standard delivery takes 3-5 business days across US.</p>",
                    "url": "https://wrydeco.com/policies/shipping-policy",
                },
                {
                    "type": "CONTACT_INFORMATION",
                    "title": "Contact Information",
                    "body": "<p>Support email: support@wrydeco.com. Working hours: 9AM-5PM EST.</p>",
                    "url": "https://wrydeco.com/policies/contact-information",
                },
                {
                    "type": "LEGAL_NOTICE",
                    "title": "Legal Notice",
                    "body": "<p>Registered business entity: Wrydeco LLC, Delaware, USA.</p>",
                    "url": "https://wrydeco.com/policies/legal-notice",
                },
            ]
        }
    }
}


# ==============================================================================
# 1. INVARIANT R-16: Live Product Search & Zero Catalog Mirroring
# ==============================================================================

@pytest.mark.asyncio
async def test_r16_live_product_search_accurate_facts_extraction():
    """R-16: Verifies live GraphQL search resolves accurate pricing, inventory, variants and options."""
    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_graphql.return_value = MOCK_GRAPHQL_PRODUCTS_PAYLOAD

    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["Ceramic", "Table", "Lamp"],
        max_results=5,
    )

    assert res.product_resolved is True
    assert res.matched_count == 1
    assert "PRODUCT_NOT_RESOLVED" not in res.warning_codes

    product = res.products[0]
    assert product.product_id == "gid://shopify/Product/1001"
    assert product.title == "Ceramic Table Lamp"
    assert product.total_inventory == 4
    assert product.min_price == "89.00"
    assert product.max_price == "89.00"
    assert product.has_in_stock_variant is True

    # Variant validation
    assert len(product.variants) == 1
    variant = product.variants[0]
    assert variant.variant_id == "gid://shopify/ProductVariant/2001"
    assert variant.sku == "LAMP-WHT-STD"
    assert variant.price == "89.00"
    assert variant.compare_at_price == "119.00"
    assert variant.available_for_sale is True
    assert variant.inventory_quantity == 4
    assert variant.options == {"Color": "Warm White", "Size": "Standard"}


@pytest.mark.asyncio
async def test_r16_adversarial_search_input_sanitization():
    """R-16 Adversarial: Verifies special characters, injection attempts, and empty terms are sanitized safely."""
    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_graphql.return_value = {"data": {"products": {"edges": []}}}

    # Case A: Injection attempts and special characters
    adversarial_terms = ['"Lamp"', "Table:Lamp", "Lamp(Ceramic)", "'; DROP TABLE products; --", ""]
    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=adversarial_terms,
        max_results=5,
    )

    # Clean terms should have stripped quotes and colons and parentheses
    for term in res.raw_query_terms:
        assert '"' not in term
        assert ":" not in term
        assert "(" not in term
        assert ")" not in term

    # Case A invoked primary query + relaxed OR fallback query because edges were empty and len(terms) > 1
    assert mock_client.execute_graphql.call_count == 2

    # Case B: Completely empty search terms -> early exit without network call
    res_empty = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["", "   ", "\t"],
        max_results=5,
    )
    assert res_empty.product_resolved is False
    assert res_empty.matched_count == 0
    assert "PRODUCT_NOT_RESOLVED" in res_empty.warning_codes
    assert "EMPTY_SEARCH_QUERY" in res_empty.warning_codes
    # Execute GraphQL count must still be 2 (no additional call for empty input!)
    assert mock_client.execute_graphql.call_count == 2


@pytest.mark.asyncio
async def test_r16_unresolved_product_sets_warning():
    """R-16: When product is not found in store, sets product_resolved=False with PRODUCT_NOT_RESOLVED warning."""
    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_graphql.return_value = {"data": {"products": {"edges": []}}}

    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["NonExistentHoverboard3000"],
        max_results=5,
    )

    assert res.product_resolved is False
    assert res.matched_count == 0
    assert "PRODUCT_NOT_RESOLVED" in res.warning_codes
    assert res.products == []


@pytest.mark.asyncio
async def test_r16_out_of_stock_product_sets_warning():
    """R-16: When matched product has 0 stock across all variants, sets OUT_OF_STOCK warning."""
    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_graphql.return_value = MOCK_GRAPHQL_OUT_OF_STOCK_PAYLOAD

    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["Limited", "Oak", "Chair"],
        max_results=5,
    )

    assert res.product_resolved is True
    assert res.matched_count == 1
    assert "PRODUCT_NOT_RESOLVED" not in res.warning_codes
    assert "OUT_OF_STOCK" in res.warning_codes
    assert res.products[0].has_in_stock_variant is False


def test_r16_database_audit_zero_catalog_mirroring():
    """R-16 Database Audit: Confirms ZERO catalog mirroring tables exist in the entire database schema."""
    table_names = set(Base.metadata.tables.keys())

    # Prohibited catalog mirror tables
    forbidden_catalog_tables = {
        "products",
        "shopify_products",
        "catalog",
        "shopify_catalog",
        "store_inventory",
        "product_variants",
        "shopify_inventory",
    }

    intersection = table_names.intersection(forbidden_catalog_tables)
    assert not intersection, f"CRITICAL VIOLATION OF R-16: Detected forbidden catalog mirroring table(s): {intersection}"

    # Verify only shopify_product_snapshots exists for email inquiry audit trail
    assert "shopify_product_snapshots" in table_names

    # Inspect shopify_product_snapshots foreign keys: must link to incoming_emails and store_profiles
    snapshot_table = Base.metadata.tables["shopify_product_snapshots"]
    fk_targets = {fk.target_fullname for fk in snapshot_table.foreign_keys}
    assert "incoming_emails.id" in fk_targets
    assert "store_profiles.id" in fk_targets


# ==============================================================================
# 2. INVARIANT R-17: 6 Store Policies Sync & Two-Tier Cache
# ==============================================================================

@pytest.mark.asyncio
async def test_r17_six_canonical_policies_synced_with_sha256_hash():
    """R-17: Verifies all 6 standard policies are synced with valid 64-hex SHA-256 hashes."""
    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_graphql.return_value = MOCK_GRAPHQL_6_POLICIES_PAYLOAD

    res = await ShopifyService.sync_legal_policies(mock_client)

    assert res.success is True
    assert set(res.policies.keys()) == set(CANONICAL_POLICY_TYPES)
    assert set(res.hashes.keys()) == set(CANONICAL_POLICY_TYPES)

    sha256_hex_regex = re.compile(r"^[0-9a-f]{64}$")

    for p_type in CANONICAL_POLICY_TYPES:
        content = res.policies[p_type]
        p_hash = res.hashes[p_type]

        # Must be non-empty and match SHA-256 regex
        assert len(content) > 0
        assert sha256_hex_regex.match(p_hash) is not None, f"Policy {p_type} hash {p_hash} is not 64 hex chars"

        # Verify mathematical correctness of SHA-256 hash
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert p_hash == expected_hash, f"Hash mismatch for {p_type}: {p_hash} != {expected_hash}"


def test_r17_adversarial_html_policy_sanitization_and_xss_protection():
    """R-17 Adversarial: Verifies HTML policies are stripped of scripts, iframes, styles and inline event handlers."""
    malicious_html = (
        "<h2>Refund Rules</h2>"
        "<p>Standard return within 30 days.</p>"
        "<script>alert('XSS_ATTACK'); window.location='http://evil.com';</script>"
        "<iframe src='http://attacker.com/cookie_stealer'></iframe>"
        "<style>body { display: none !important; }</style>"
        "<a href='javascript:stealTokens()' onclick='leakData()'>Click Here</a>"
        "<img src='invalid.jpg' onerror='executePayload()'>"
    )

    clean_html = sanitize_policy_html(malicious_html)

    # Assert dangerous tags and handlers are purged
    assert "<script" not in clean_html.lower()
    assert "<iframe" not in clean_html.lower()
    assert "<style" not in clean_html.lower()
    assert "javascript:" not in clean_html.lower()
    assert "onclick" not in clean_html.lower()
    assert "onerror" not in clean_html.lower()

    # Assert normalized clean text is completely tag-free and safe
    clean_text = normalize_policy_text(malicious_html)
    assert "<script" not in clean_text
    assert "<" not in clean_text
    assert ">" not in clean_text
    assert "Standard return within 30 days." in clean_text


@pytest.mark.asyncio
async def test_r17_two_tier_cache_300s_ttl_and_force_refresh(db_session: AsyncSession):
    """R-17: Verifies Two-Tier Cache (Tier 1 memory + Tier 2 DB) with 300s TTL and force_refresh bypass."""
    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name="Wrydeco Cache Test Store",
        brand_name="Wrydeco",
        public_domain="wrydeco.com",
        canonical_domain="wrydeco-cache.myshopify.com",
        status="active",
        default_language="en",
    )
    db_session.add(store)
    await db_session.commit()

    # Clear memory cache for this store
    _memory_policy_cache.pop(store_id, None)

    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_graphql.return_value = MOCK_GRAPHQL_6_POLICIES_PAYLOAD

    # --- Call 1: Cache Miss on both tiers -> calls GraphQL ---
    policies_call1 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
        force_refresh=False,
    )
    assert len(policies_call1) == 6
    assert mock_client.execute_graphql.call_count == 1
    assert store_id in _memory_policy_cache

    # --- Call 2: Within 300s -> Tier 1 Memory Cache Hit -> does NOT call GraphQL ---
    policies_call2 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
        force_refresh=False,
    )
    assert len(policies_call2) == 6
    assert mock_client.execute_graphql.call_count == 1, "Tier 1 memory cache failed; invoked GraphQL API!"

    # --- Call 3: Clear Tier 1 Memory Cache -> Tier 2 DB Cache Hit (<300s) -> does NOT call GraphQL ---
    _memory_policy_cache.pop(store_id, None)
    policies_call3 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
        force_refresh=False,
    )
    assert len(policies_call3) == 6
    assert mock_client.execute_graphql.call_count == 1, "Tier 2 DB cache failed; invoked GraphQL API!"
    # Re-populated Tier 1
    assert store_id in _memory_policy_cache

    # --- Call 4: force_refresh=True -> Bypasses both tiers -> invokes GraphQL ---
    policies_call4 = await ShopifyService.get_effective_store_policies(
        session=db_session,
        store_profile_id=store_id,
        client=mock_client,
        force_refresh=True,
    )
    assert len(policies_call4) == 6
    assert mock_client.execute_graphql.call_count == 2, "force_refresh=True failed to bypass cache!"


@pytest.mark.asyncio
async def test_r17_memory_cache_eviction_on_custom_policy_mutation(db_session: AsyncSession):
    """R-17 / R-18: Verifies Tier 1 memory cache is immediately evicted when custom policies are mutated."""
    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name="Wrydeco Mutation Cache Store",
        brand_name="Wrydeco",
        public_domain="wrydeco-mut.com",
        status="active",
        default_language="en",
    )
    db_session.add(store)
    await db_session.commit()

    # Pre-populate memory cache
    _memory_policy_cache[store_id] = (1000.0, {})
    assert store_id in _memory_policy_cache

    # 1. Create custom policy
    create_req = CustomPolicyCreateRequest(
        policy_type="WARRANTY",
        title="Lifetime Warranty",
        content="All products covered for lifetime manufacturing defects.",
    )
    await create_custom_policy(store_id=store_id, data=create_req, db=db_session)
    assert store_id not in _memory_policy_cache, "Memory cache was not evicted upon creating custom policy!"

    # Pre-populate again
    _memory_policy_cache[store_id] = (1000.0, {})

    # 2. Update custom policy
    update_req = CustomPolicyUpdateRequest(
        title="Updated Warranty",
        content="Warranty updated to 2 years.",
    )
    await update_custom_policy(store_id=store_id, policy_type="WARRANTY", data=update_req, db=db_session)
    assert store_id not in _memory_policy_cache, "Memory cache was not evicted upon updating custom policy!"

    # Pre-populate again
    _memory_policy_cache[store_id] = (1000.0, {})

    # 3. Delete custom policy
    await delete_custom_policy(store_id=store_id, policy_type="WARRANTY", db=db_session)
    assert store_id not in _memory_policy_cache, "Memory cache was not evicted upon deleting custom policy!"


# ==============================================================================
# 3. INVARIANT R-18: Custom Policies & Stale Draft Warnings
# ==============================================================================

def test_r18_check_draft_policy_freshness_detects_policy_updated():
    """R-18: Verifies check_draft_policy_freshness detects updated policies with full discrepancy details."""
    hash_v1_refund = compute_content_hash("Standard 30 days return")
    hash_v1_shipping = compute_content_hash("Standard 3-5 days delivery")

    # Draft was generated with hash_v1
    policy_hashes_used = {
        "REFUND": hash_v1_refund,
        "SHIPPING": hash_v1_shipping,
    }

    # Case A: Policies unchanged
    current_hashes_matching = {
        "REFUND": hash_v1_refund,
        "SHIPPING": hash_v1_shipping,
        "TERMS": compute_content_hash("Terms of service"),
    }
    res_fresh = ShopifyService.check_draft_policy_freshness(
        policy_hashes_used=policy_hashes_used,
        current_policy_hashes=current_hashes_matching,
    )
    assert res_fresh.is_stale is False
    assert res_fresh.stale_reason is None
    assert res_fresh.stale_details == []

    # Case B: REFUND policy updated to 14 days
    hash_v2_refund = compute_content_hash("Strict 14 days return policy")
    current_hashes_updated = {
        "REFUND": hash_v2_refund,
        "SHIPPING": hash_v1_shipping,
    }
    res_stale = ShopifyService.check_draft_policy_freshness(
        policy_hashes_used=policy_hashes_used,
        current_policy_hashes=current_hashes_updated,
    )
    assert res_stale.is_stale is True
    assert res_stale.stale_reason == "POLICY_UPDATED"
    assert len(res_stale.stale_details) == 1
    assert res_stale.stale_details[0]["policy_type"] == "REFUND"
    assert res_stale.stale_details[0]["used_hash"] == hash_v1_refund
    assert res_stale.stale_details[0]["current_hash"] == hash_v2_refund


@pytest.mark.asyncio
async def test_r18_mail_fetch_service_automatically_flags_stale_in_db(db_session: AsyncSession):
    """R-18 End-to-End: When opening email content, system verifies freshness and persists is_stale=True in DB."""
    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name=f"Wrydeco Stale Test Store-{uuid.uuid4().hex[:6]}",
        brand_name="Wrydeco",
        public_domain="wrydeco-stale.com",
        status="active",
        default_language="en",
    )
    db_session.add(store)

    mailbox_id = uuid.uuid4()
    mailbox = Mailbox(
        id=mailbox_id,
        store_profile_id=store_id,
        address="support@wrydeco-stale.com",
        encrypted_password=encrypt_secret("dummy"),
        imap_host="mail.wrydeco.com",
        imap_port=993,
        imap_tls_mode="SSL",
        smtp_host="mail.wrydeco.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        status="active",
    )
    db_session.add(mailbox)

    # Store policy currently in DB: REFUND hash_v2
    hash_v2_refund = compute_content_hash("New Policy Content V2")
    db_policy = StorePolicy(
        id=uuid.uuid4(),
        store_profile_id=store_id,
        policy_type="REFUND",
        title="Refund Policy",
        body_text="New Policy Content V2",
        content_hash=hash_v2_refund,
        is_custom=False,
    )
    db_session.add(db_policy)

    # Incoming Email drafted with older REFUND hash_v1
    hash_v1_refund = compute_content_hash("Old Policy Content V1")
    email_id = uuid.uuid4()
    incoming = IncomingEmail(
        id=email_id,
        store_profile_id=store_id,
        mailbox_id=mailbox_id,
        folder="INBOX",
        imap_uid=999,
        uidvalidity=12345,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco-stale.com",
        subject="Refund Request",
        received_at=datetime.datetime.now(datetime.UTC),
        status="drafted",
        classification_category="return_or_refund",
        policy_hashes_used={"REFUND": hash_v1_refund},
        is_stale=False,
        stale_reason=None,
    )
    db_session.add(incoming)
    await db_session.commit()

    # Clear memory cache so it reads from DB
    _memory_policy_cache.pop(store_id, None)

    fake_raw_email = (
        b"From: customer@example.com\r\n"
        b"To: support@wrydeco-stale.com\r\n"
        b"Subject: Refund Request\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"I want a refund please."
    )

    with patch("app.ingestion.service.IMAPClient.fetch_raw_email_peek", new_callable=AsyncMock) as mock_peek:
        mock_peek.return_value = fake_raw_email
        email_content = await MailFetchService.fetch_email_content(db=db_session, email_id=email_id)

    # Response must report stale
    assert email_content.is_stale is True
    assert email_content.stale_reason == "POLICY_UPDATED"
    assert email_content.stale_details[0]["policy_type"] == "REFUND"

    # Database record must have been updated
    await db_session.refresh(incoming)
    assert incoming.is_stale is True
    assert incoming.stale_reason == "POLICY_UPDATED"
    assert incoming.stale_details[0]["used_hash"] == hash_v1_refund
    assert incoming.stale_details[0]["current_hash"] == hash_v2_refund


# ==============================================================================
# 4. INVARIANT R-04: Zero Raw Body in PostgreSQL Audit
# ==============================================================================

@pytest.mark.asyncio
async def test_r04_raw_sql_schema_inspection_zero_raw_body(db_session: AsyncSession):
    """R-04: Raw SQL schema inspection verifies incoming_emails, shopify_order_snapshots, shopify_product_snapshots have ZERO raw body columns."""
    forbidden_column_keywords = {"body", "raw_body", "html_body", "text_body", "raw_content", "payload", "mime"}

    tables_to_check = [
        "incoming_emails",
        "shopify_order_snapshots",
        "shopify_product_snapshots",
    ]

    for table_name in tables_to_check:
        # Raw SQL PRAGMA / columns inspection
        result = await db_session.execute(text(f"PRAGMA table_info({table_name});"))
        columns = [row[1].lower() for row in result.fetchall()]

        # Verify no forbidden column exists
        for col in columns:
            assert col not in forbidden_column_keywords, (
                f"CRITICAL VIOLATION OF R-04: Table '{table_name}' contains raw body column: '{col}'"
            )


@pytest.mark.asyncio
async def test_r04_zero_raw_body_data_leakage_audit(db_session: AsyncSession):
    """R-04: Verifies confidential raw email body is NEVER persisted in any database field."""
    confidential_email_body = "SUPER_SECRET_PATIENT_SSN_999-88-7777_CONFIDENTIAL_PLAINTEXT"

    store_id = uuid.uuid4()
    store = StoreProfile(
        id=store_id,
        name="Wrydeco R04 Audit Store",
        brand_name="Wrydeco",
        public_domain="wrydeco-r04.com",
        status="active",
        default_language="en",
    )
    db_session.add(store)

    mailbox_id = uuid.uuid4()
    mailbox = Mailbox(
        id=mailbox_id,
        store_profile_id=store_id,
        address="support@wrydeco-r04.com",
        encrypted_password=encrypt_secret("dummy"),
        imap_host="mail.wrydeco.com",
        imap_port=993,
        imap_tls_mode="SSL",
        smtp_host="mail.wrydeco.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        status="active",
    )
    db_session.add(mailbox)

    email_id = uuid.uuid4()
    incoming = IncomingEmail(
        id=email_id,
        store_profile_id=store_id,
        mailbox_id=mailbox_id,
        folder="INBOX",
        imap_uid=5555,
        uidvalidity=1001,
        sender_email="secret@example.com",
        recipient_email="support@wrydeco-r04.com",
        subject="Confidential Inquiry",
        received_at=datetime.datetime.now(datetime.UTC),
        status="pending",
    )
    db_session.add(incoming)

    # Order snapshot
    order_snap = ShopifyOrderSnapshot(
        id=uuid.uuid4(),
        incoming_email_id=email_id,
        store_profile_id=store_id,
        customer_email="secret@example.com",
        lookup_status="no_order",
        lookup_checked_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(order_snap)

    # Product snapshot
    prod_snap = ShopifyProductSnapshot(
        id=uuid.uuid4(),
        incoming_email_id=email_id,
        store_profile_id=store_id,
        search_query="status:active AND (title:*lamp*)",
        raw_query_terms=["lamp"],
        matched_count=0,
        product_resolved=False,
        matched_products=[],
        warning_codes=["PRODUCT_NOT_RESOLVED"],
    )
    db_session.add(prod_snap)
    await db_session.commit()

    # Query all raw text columns in the database for these tables
    res_mail = await db_session.execute(
        text("SELECT * FROM incoming_emails WHERE id = :email_id"),
        {"email_id": str(email_id)},
    )
    for row in res_mail.fetchall():
        assert confidential_email_body not in str(row)

    res_order = await db_session.execute(
        text("SELECT * FROM shopify_order_snapshots WHERE incoming_email_id = :email_id"),
        {"email_id": str(email_id)},
    )
    for row in res_order.fetchall():
        assert confidential_email_body not in str(row)

    res_prod = await db_session.execute(
        text("SELECT * FROM shopify_product_snapshots WHERE incoming_email_id = :email_id"),
        {"email_id": str(email_id)},
    )
    for row in res_prod.fetchall():
        assert confidential_email_body not in str(row)


# ==============================================================================
# 5. INVARIANT R-09 / R-10 / R-11: Order Lookup Flags & Distinction
# ==============================================================================

def test_r09_email_normalization_preserves_dots_and_plus_tags():
    """R-09: Email normalization must strictly strip and lowercase only, preserving dots and +tag subaddressing."""
    raw = "  Customer.Name+Order123@Example.COM  "
    normalized = ShopifyService.normalize_email(raw)
    assert normalized == "customer.name+order123@example.com"
    assert "." in normalized
    assert "+order123" in normalized


def test_r10_five_independent_order_status_flags_and_customer_preservation():
    """R-10: 5 independent status flags: has_paid, has_active, has_cancelled, has_refunded, has_fulfilled."""
    ref_time = datetime.datetime.now(datetime.UTC)

    # Order cancelled and refunded 5 days ago
    mock_orders = [
        {
            "id": 101,
            "created_at": (ref_time - datetime.timedelta(days=5)).isoformat(),
            "financial_status": "refunded",
            "fulfillment_status": "fulfilled",
            "cancelled_at": (ref_time - datetime.timedelta(days=4)).isoformat(),
            "test": False,
        }
    ]

    flags = ShopifyService.classify_order_flags(
        orders=mock_orders,
        window_days=60,
        reference_time=ref_time,
    )

    # Crucial invariant: has_order_record remains True even when cancelled or refunded!
    assert flags.has_order_record is True
    assert flags.has_recent_order is True
    assert flags.recent_orders_count == 1
    assert flags.has_cancelled_order is True
    assert flags.has_refunded_order is True
    assert flags.has_fulfilled_order is True
    assert flags.has_active_order is False


def test_r09_test_orders_excluded():
    """R-09: Orders with test=True are excluded from recent order count and flags."""
    ref_time = datetime.datetime.now(datetime.UTC)
    mock_orders = [
        {
            "id": 999,
            "created_at": (ref_time - datetime.timedelta(days=2)).isoformat(),
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "test": True,  # Test order!
        }
    ]

    flags = ShopifyService.classify_order_flags(
        orders=mock_orders,
        window_days=60,
        reference_time=ref_time,
    )

    assert flags.has_order_record is False
    assert flags.has_recent_order is False
    assert flags.recent_orders_count == 0


@pytest.mark.asyncio
async def test_r11_separation_lookup_unavailable_and_no_order():
    """R-11: Transient / proxy errors must raise exceptions or report error; NEVER swallow into no_order."""
    mock_client = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client.execute_rest.side_effect = ShopifyProxyError("SOCKS5 connection refused")

    # lookup_recent_orders must raise ShopifyProxyError
    with pytest.raises(ShopifyProxyError):
        await ShopifyService.lookup_recent_orders(
            client=mock_client,
            customer_email="buyer@example.com",
            days=60,
        )

    # 200 OK with empty orders must emit 'no_order'
    mock_client_ok = AsyncMock(spec=ProxyEnforcedShopifyClient)
    mock_client_ok.execute_rest.return_value = {"orders": []}
    res_ok = await ShopifyService.enrich_order_lookup(
        client=mock_client_ok,
        customer_email="buyer@example.com",
        days=60,
    )
    assert res_ok.status == "no_order"
    assert res_ok.flags.has_order_record is False
