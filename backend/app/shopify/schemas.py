"""Schemas for Shopify connection, order lookup, product facts, and policy synchronization.

INVARIANTS:
- R-09: 60-day order lookup window, normalized email, test orders excluded.
- R-10: 5 independent boolean status flags, preservation of has_order_record on cancelled/refunded.
- R-11: Explicit distinction between lookup_unavailable and no_order.
- R-16: Live Product Search, Zero Catalog Mirroring, Product not resolved alert.
- R-17: 6 standard policy types synchronization with SHA-256 integrity hash.
- R-18: Custom policies management and Stale draft warnings.
"""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field


class ShopifyCandidateTestRequest(BaseModel):
    shop_domain: str = Field(..., description="Canonical domain, e.g. wrydeco.myshopify.com")
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)
    proxy_profile_id: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None


class ShopifyConnectionTestResult(BaseModel):
    success: bool
    shop_name: str | None = None
    shop_domain: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    token_expires_at: datetime.datetime | None = None
    latency_ms: int | None = None
    error: str | None = None
    detail: str | None = None


class ShopifyOrderLineItem(BaseModel):
    id: int | str
    title: str
    quantity: int
    price: str | float


class ShopifyOrderSummary(BaseModel):
    id: int | str
    name: str  # e.g. #1001
    email: str
    created_at: datetime.datetime
    financial_status: str
    fulfillment_status: str | None = None
    total_price: str
    currency: str
    line_items: list[ShopifyOrderLineItem] = Field(default_factory=list)


class OrderClassificationFlags(BaseModel):
    """5 independent boolean order flags preserving customer relationship (Invariant R-10)."""

    has_order_record: bool = Field(default=False, description="True if customer has any order in Shopify")
    has_recent_order: bool = Field(default=False, description="True if customer has an order within last 60 days")
    recent_orders_count: int = Field(default=0, description="Total number of orders within last 60 days")
    has_paid_order: bool = Field(default=False, description="True if any order is paid or partially refunded")
    has_active_order: bool = Field(default=False, description="True if order is processing/in-transit and not cancelled/refunded")
    has_cancelled_order: bool = Field(default=False, description="True if customer has a cancelled order")
    has_refunded_order: bool = Field(default=False, description="True if customer has a refunded order")
    has_fulfilled_order: bool = Field(default=False, description="True if order is completely fulfilled")


class ShopifyOrderLookupResult(BaseModel):
    """Complete result of 60-day customer order lookup (Invariant R-09, R-10, R-11)."""

    customer_email: str
    status: str = Field(..., description="'success', 'no_order', or 'lookup_unavailable'")
    flags: OrderClassificationFlags
    latest_order: ShopifyOrderSummary | None = None
    all_recent_orders: list[ShopifyOrderSummary] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None


# Product Search Schemas (Invariant R-16)
class ProductVariantFact(BaseModel):
    """Normalized facts for a single product variant."""

    variant_id: str = Field(..., description="Shopify GID or REST ID of the variant")
    title: str = Field(..., description="Variant display title, e.g. 'White / Large'")
    sku: str | None = Field(default=None, description="SKU code if assigned")
    price: str = Field(..., description="Current selling price as decimal string, e.g. '89.00'")
    compare_at_price: str | None = Field(default=None, description="Original price before discount if on sale")
    currency: str = Field(default="USD", description="Currency code (e.g. USD, EUR, VND)")
    available_for_sale: bool = Field(default=True, description="True if customer can purchase right now")
    inventory_quantity: int | None = Field(default=None, description="Physical stock level if available")
    options: dict[str, str] = Field(default_factory=dict, description="Key-value mapping of options, e.g. {'Color': 'White'}")


class ProductFact(BaseModel):
    """Normalized facts for a Shopify product matched during inquiry."""

    product_id: str = Field(..., description="Shopify GID or numeric ID")
    title: str = Field(..., description="Official product title")
    handle: str = Field(..., description="URL slug of the product")
    vendor: str | None = Field(default=None, description="Brand or vendor name")
    product_type: str | None = Field(default=None, description="Category/Type of product")
    status: str = Field(default="ACTIVE", description="Product status: ACTIVE, ARCHIVED, DRAFT")
    description_snippet: str | None = Field(default=None, description="Sanitized, truncated text description")
    online_store_url: str | None = Field(default=None, description="Direct URL to product on public store")
    featured_image_url: str | None = Field(default=None, description="Primary product image URL")
    total_inventory: int | None = Field(default=None, description="Aggregated inventory across all variants")
    min_price: str = Field(..., description="Lowest variant price")
    max_price: str = Field(..., description="Highest variant price")
    has_in_stock_variant: bool = Field(default=True, description="True if at least one variant is available for sale")
    variants: list[ProductVariantFact] = Field(default_factory=list, description="List of top variants (max 10)")


class ProductSearchSnapshotData(BaseModel):
    """Complete product search resolution snapshot ready for audit and draft prompt injection."""

    search_query: str = Field(..., description="Sanitized search query executed against Shopify")
    raw_query_terms: list[str] = Field(default_factory=list, description="Original product terms extracted from email")
    matched_count: int = Field(default=0, description="Number of products matched")
    product_resolved: bool = Field(default=False, description="True if at least one suitable product was identified")
    warning_codes: list[str] = Field(default_factory=list, description="Warning codes: PRODUCT_NOT_RESOLVED, OUT_OF_STOCK, etc.")
    products: list[ProductFact] = Field(default_factory=list, description="Top matched products (max 5)")
    execution_time_ms: int = Field(default=0, description="Latency in milliseconds for the proxy request")


class ShopifyProductSummary(BaseModel):
    id: int | str
    title: str
    status: str
    variants: list[dict[str, Any]] = Field(default_factory=list)


# Policy Schemas (Invariant R-17, R-18)
class StorePolicyItem(BaseModel):
    id: str
    store_profile_id: str
    policy_type: str
    title: str
    body_text: str
    body_html: str | None = None
    content_hash: str
    url: str | None = None
    is_custom: bool = False
    synced_at: datetime.datetime


class CustomPolicyCreateRequest(BaseModel):
    policy_type: str = Field(..., min_length=2, max_length=50, description="e.g. WARRANTY, CANCELLATION")
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)


class CustomPolicyUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)


class PolicyDict(dict):
    """Dictionary supporting both canonical uppercase keys (e.g. REFUND) and legacy snake_case aliases (e.g. refund_policy)."""

    _ALIAS_MAP: dict[str, str] = {
        "refund_policy": "REFUND",
        "privacy_policy": "PRIVACY",
        "terms_of_service": "TERMS",
        "shipping_policy": "SHIPPING",
        "contact_information": "CONTACT",
        "legal_notice": "LEGAL",
    }

    def __getitem__(self, key: str) -> Any:
        if super().__contains__(key):
            return super().__getitem__(key)
        mapped = self._ALIAS_MAP.get(key) or self._ALIAS_MAP.get(key.lower()) or key.upper()
        if super().__contains__(mapped):
            return super().__getitem__(mapped)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            mapped = self._ALIAS_MAP.get(key) or self._ALIAS_MAP.get(key.lower()) or key.upper()
            return super().__contains__(mapped)
        return False


class ShopifyPoliciesSyncResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    success: bool
    policies: PolicyDict | dict[str, str] = Field(default_factory=PolicyDict)
    hashes: PolicyDict | dict[str, str] = Field(default_factory=PolicyDict)
    error: str | None = None


class StaleDraftCheckResult(BaseModel):
    is_stale: bool = False
    stale_reason: str | None = None
    stale_details: list[dict[str, Any]] = Field(default_factory=list)
