"""Tests for Live Shopify Product Search, Variant Facts Extraction, and Zero Catalog Mirroring.

INVARIANTS & SPECIFICATIONS:
- Invariant R-16: Live Product Search on-demand via GraphQL over SOCKS5 Proxy.
  Zero Catalog Mirroring. When no product matches, sets product_resolved=False
  and adds warning PRODUCT_NOT_RESOLVED to prevent AI hallucinations.
- Invariant R-02: Fail-closed proxy behavior; re-raises ShopifyProxyError.
- Queue service product enrichment: creates immutable audit snapshot ShopifyProductSnapshot
  and advances pipeline to draft generation.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from app.db.models.email import EmailJob, IncomingEmail
from app.db.models.shopify import ShopifyProductSnapshot
from app.db.models.store import Mailbox, StoreProfile
from app.queue.service import TransactionalQueueService
from app.shopify.exceptions import ShopifyProxyError, ShopifyTransientError
from app.shopify.service import ShopifyService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SAMPLE_GRAPHQL_PRODUCT_RESPONSE = {
    "data": {
        "products": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/Product/1234567890",
                        "title": "Vintage Leather Jacket",
                        "handle": "vintage-leather-jacket",
                        "description": "High quality real leather vintage style jacket with zipper.",
                        "vendor": "Wrydeco Studio",
                        "productType": "Apparel",
                        "status": "ACTIVE",
                        "tags": ["vintage", "leather", "jacket", "outerwear"],
                        "totalInventory": 15,
                        "priceRangeV2": {
                            "minVariantPrice": {"amount": "129.99", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "149.99", "currencyCode": "USD"},
                        },
                        "variants": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "gid://shopify/ProductVariant/987654321",
                                        "title": "Black / Large",
                                        "sku": "VLJ-BLK-L",
                                        "price": "129.99",
                                        "compareAtPrice": "159.99",
                                        "availableForSale": True,
                                        "inventoryQuantity": 10,
                                        "selectedOptions": [
                                            {"name": "Color", "value": "Black"},
                                            {"name": "Size", "value": "Large"},
                                        ],
                                    }
                                },
                                {
                                    "node": {
                                        "id": "gid://shopify/ProductVariant/987654322",
                                        "title": "Brown / Medium",
                                        "sku": "VLJ-BRN-M",
                                        "price": "149.99",
                                        "compareAtPrice": None,
                                        "availableForSale": True,
                                        "inventoryQuantity": 5,
                                        "selectedOptions": [
                                            {"name": "Color", "value": "Brown"},
                                            {"name": "Size", "value": "Medium"},
                                        ],
                                    }
                                },
                            ]
                        },
                    }
                }
            ]
        }
    }
}


@pytest.mark.asyncio
async def test_search_products_live_success_extracts_full_facts() -> None:
    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = SAMPLE_GRAPHQL_PRODUCT_RESPONSE

    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["Vintage", "Leather", "Jacket"],
        max_results=5,
    )

    assert res.product_resolved is True
    assert res.matched_count == 1
    assert len(res.products) == 1
    assert res.warning_codes == []

    prod = res.products[0]
    assert prod.product_id == "gid://shopify/Product/1234567890"
    assert prod.title == "Vintage Leather Jacket"
    assert prod.vendor == "Wrydeco Studio"
    assert prod.total_inventory == 15
    assert prod.min_price == "129.99"
    assert prod.max_price == "149.99"
    assert len(prod.variants) == 2

    v1 = prod.variants[0]
    assert v1.title == "Black / Large"
    assert v1.sku == "VLJ-BLK-L"
    assert v1.price == "129.99"
    assert v1.compare_at_price == "159.99"
    assert v1.currency == "USD"
    assert v1.available_for_sale is True
    assert v1.inventory_quantity == 10
    assert v1.options == {"Color": "Black", "Size": "Large"}


@pytest.mark.asyncio
async def test_search_products_live_zero_results_adds_product_not_resolved_warning() -> None:
    """INVARIANT R-16: 0 results must flag product_resolved=False and PRODUCT_NOT_RESOLVED."""
    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = {"data": {"products": {"edges": []}}}

    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["NonExistentCosplayItem123"],
    )

    assert res.product_resolved is False
    assert res.matched_count == 0
    assert len(res.products) == 0
    assert "PRODUCT_NOT_RESOLVED" in res.warning_codes


@pytest.mark.asyncio
async def test_search_products_live_out_of_stock_adds_warning() -> None:
    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = {
        "data": {
            "products": {
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/Product/999",
                            "title": "Limited Edition Figurine",
                            "handle": "limited-figurine",
                            "description": "Sold out collectible item.",
                            "vendor": "Wrydeco Studio",
                            "productType": "Collectible",
                            "status": "ACTIVE",
                            "tags": ["collectible"],
                            "totalInventory": 0,
                            "priceRangeV2": {
                                "minVariantPrice": {"amount": "89.99", "currencyCode": "USD"},
                                "maxVariantPrice": {"amount": "89.99", "currencyCode": "USD"},
                            },
                            "variants": {
                                "edges": [
                                    {
                                        "node": {
                                            "id": "gid://shopify/ProductVariant/888",
                                            "title": "Default Title",
                                            "sku": "LEF-001",
                                            "price": "89.99",
                                            "compareAtPrice": None,
                                            "availableForSale": False,
                                            "inventoryQuantity": 0,
                                            "selectedOptions": [],
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

    res = await ShopifyService.search_products_live(
        client=mock_client,
        search_terms=["Limited", "Figurine"],
    )

    assert res.product_resolved is True
    assert res.matched_count == 1
    assert "OUT_OF_STOCK" in res.warning_codes


@pytest.mark.asyncio
async def test_search_products_live_fail_closed_on_proxy_error() -> None:
    """INVARIANT R-02: Fail-closed proxy behavior."""
    mock_client = AsyncMock()
    mock_client.execute_graphql.side_effect = ShopifyProxyError("SOCKS5 proxy connection lost")

    with pytest.raises(ShopifyProxyError) as exc_info:
        await ShopifyService.search_products_live(
            client=mock_client,
            search_terms=["Leather", "Jacket"],
        )

    assert "SOCKS5 proxy connection lost" in str(exc_info.value)


@pytest.mark.asyncio
async def test_queue_product_enrichment_creates_audit_snapshot_and_schedules_draft(
    db_session: AsyncSession,
) -> None:
    """Full Queue Service integration test for job_type='enrich_product'."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name=f"test-store-prod-{uuid.uuid4().hex[:6]}",
        brand_name="Wrydeco Store",
        public_domain="wrydeco.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password="enc",
    )
    db_session.add(mailbox)

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer@example.com",
        recipient_email="support@wrydeco.com",
        subject="Do you have the Vintage Leather Jacket in Large?",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=2001,
        uidvalidity=1,
        status="classified",
        classification_category="product_inquiry",
        customer_status="no_order",
        extracted_entities={"product_names": ["Vintage Leather Jacket"]},
    )
    db_session.add(email_rec)

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_rec.id,
        store_profile_id=store.id,
        job_type="enrich_product",
        status="processing",
        attempts=1,
    )
    db_session.add(job)
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = SAMPLE_GRAPHQL_PRODUCT_RESPONSE

    with patch.object(TransactionalQueueService, "_get_shopify_client_for_store", return_value=mock_client):
        success = await TransactionalQueueService.process_product_enrichment_job(
            session=db_session,
            job_id=job.id,
        )

    assert success is True

    # Refresh objects from DB
    await db_session.refresh(job)
    await db_session.refresh(email_rec)

    assert job.status == "completed"
    assert email_rec.product_snapshot_id is not None

    # Verify audit snapshot
    stmt_snap = select(ShopifyProductSnapshot).where(ShopifyProductSnapshot.id == email_rec.product_snapshot_id)
    snapshot = (await db_session.execute(stmt_snap)).scalar_one()

    assert snapshot.product_resolved is True
    assert snapshot.matched_count == 1
    assert "Vintage Leather Jacket" in snapshot.search_query
    assert snapshot.raw_query_terms == ["Vintage Leather Jacket"]
    assert len(snapshot.matched_products) == 1
    assert snapshot.matched_products[0]["title"] == "Vintage Leather Jacket"

    # Verify next job 'generate_draft' was queued
    stmt_next = select(EmailJob).where(
        EmailJob.incoming_email_id == email_rec.id,
        EmailJob.job_type == "generate_draft",
    )
    next_job = (await db_session.execute(stmt_next)).scalar_one_or_none()
    assert next_job is not None
    assert next_job.status == "queued"


@pytest.mark.asyncio
async def test_queue_product_enrichment_unresolved_product_creates_snapshot_with_warning(
    db_session: AsyncSession,
) -> None:
    """When GraphQL returns 0 products, the snapshot preserves PRODUCT_NOT_RESOLVED warning."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name=f"test-store-unres-{uuid.uuid4().hex[:6]}",
        brand_name="Wrydeco Store",
        public_domain="wrydeco.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password="enc",
    )
    db_session.add(mailbox)

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer2@example.com",
        recipient_email="support@wrydeco.com",
        subject="Looking for Unknown Cyberpunk Boots",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=2002,
        uidvalidity=1,
        status="classified",
        classification_category="product_inquiry",
        customer_status="no_order",
        extracted_entities={"product_names": ["Unknown Cyberpunk Boots"]},
    )
    db_session.add(email_rec)

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_rec.id,
        store_profile_id=store.id,
        job_type="enrich_product",
        status="processing",
        attempts=1,
    )
    db_session.add(job)
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.execute_graphql.return_value = {"data": {"products": {"edges": []}}}

    with patch.object(TransactionalQueueService, "_get_shopify_client_for_store", return_value=mock_client):
        success = await TransactionalQueueService.process_product_enrichment_job(
            session=db_session,
            job_id=job.id,
        )

    assert success is True
    await db_session.refresh(email_rec)
    assert email_rec.product_snapshot_id is not None

    stmt_snap = select(ShopifyProductSnapshot).where(ShopifyProductSnapshot.id == email_rec.product_snapshot_id)
    snapshot = (await db_session.execute(stmt_snap)).scalar_one()

    assert snapshot.product_resolved is False
    assert snapshot.matched_count == 0
    assert "PRODUCT_NOT_RESOLVED" in snapshot.warning_codes


@pytest.mark.asyncio
async def test_queue_product_enrichment_transient_error_schedules_retry(
    db_session: AsyncSession,
) -> None:
    """When GraphQL fails with transient proxy error, job is re-queued with exponential backoff."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name=f"test-store-retry-{uuid.uuid4().hex[:6]}",
        brand_name="Wrydeco Store",
        public_domain="wrydeco.com",
        status="active",
    )
    db_session.add(store)

    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@wrydeco.com",
        encrypted_password="enc",
    )
    db_session.add(mailbox)

    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        sender_email="customer3@example.com",
        recipient_email="support@wrydeco.com",
        subject="Where is my product?",
        received_at=datetime.datetime.now(datetime.UTC),
        imap_uid=2003,
        uidvalidity=1,
        status="classified",
        classification_category="product_inquiry",
    )
    db_session.add(email_rec)

    job = EmailJob(
        id=uuid.uuid4(),
        incoming_email_id=email_rec.id,
        store_profile_id=store.id,
        job_type="enrich_product",
        status="processing",
        attempts=1,
    )
    db_session.add(job)
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.execute_graphql.side_effect = ShopifyTransientError("502 Bad Gateway")

    with patch.object(TransactionalQueueService, "_get_shopify_client_for_store", return_value=mock_client):
        success = await TransactionalQueueService.process_product_enrichment_job(
            session=db_session,
            job_id=job.id,
        )

    assert success is False
    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.scheduled_at is not None
