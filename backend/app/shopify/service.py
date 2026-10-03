"""Shopify business service for connection testing, policy sync, live product search, and 60-day order lookups.

INVARIANTS:
- R-02 / R-13: 100% Shopify traffic strictly routed through SOCKS5 proxy (socks5h://), fail-closed.
- R-09: 60-day order lookup window, normalized email (trim + lower only), test orders excluded.
- R-10: 5 independent boolean status flags, preservation of has_order_record on cancelled/refunded.
- R-11: Explicit distinction between lookup_unavailable and no_order. Never swallow errors into no_order.
- R-16: Live product search, No Catalog Mirroring, Product not resolved alert.
- R-17: 6 standard policy types sync, HTML sanitization, clean text normalization, 64-hex SHA-256 hash, 300s cache.
- R-18: Custom policies support and Stale draft warnings.
"""

from __future__ import annotations

import datetime
import re
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.store import StorePolicy
from app.proxy.client import ResolvedProxyConfig
from app.shopify.cleaner import compute_content_hash, normalize_policy_text
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyProxyError,
)
from app.shopify.schemas import (
    OrderClassificationFlags,
    PolicyDict,
    ProductFact,
    ProductSearchSnapshotData,
    ProductVariantFact,
    ShopifyConnectionTestResult,
    ShopifyOrderLineItem,
    ShopifyOrderLookupResult,
    ShopifyOrderSummary,
    ShopifyPoliciesSyncResult,
    StaleDraftCheckResult,
    StorePolicyItem,
)
from app.shopify.token import ShopifyTokenManager

GRAPHQL_SHOP_IDENTITY = """
query GetShopIdentity {
  shop {
    id
    name
    myshopifyDomain
  }
}
"""

GRAPHQL_POLICIES_PRIMARY = """
query GetShopPolicies {
  shop {
    shopPolicies {
      id
      title
      body
      type
      url
    }
  }
}
"""

GRAPHQL_POLICIES_FALLBACK = """
query GetShopPoliciesFallback {
  shop {
    refundPolicy { id title body url }
    privacyPolicy { id title body url }
    termsOfService { id title body url }
    shippingPolicy { id title body url }
    contactInformation { id title body url }
    legalNotice { id title body url }
  }
}
"""

GRAPHQL_SEARCH_PRODUCTS = """
query SearchProductsLive($query: String!, $first: Int = 5) {
  products(first: $first, query: $query) {
    edges {
      node {
        id
        title
        handle
        vendor
        productType
        status
        description(truncateAt: 300)
        onlineStoreUrl
        featuredImage {
          url
          altText
        }
        totalInventory
        variants(first: 10) {
          edges {
            node {
              id
              title
              sku
              price
              compareAtPrice
              availableForSale
              inventoryQuantity
              selectedOptions {
                name
                value
              }
            }
          }
        }
      }
    }
  }
}
"""

CANONICAL_POLICY_TYPES = ["REFUND", "PRIVACY", "TERMS", "SHIPPING", "CONTACT", "LEGAL"]

POLICY_TYPE_MAP: dict[str, str] = {
    "REFUND_POLICY": "REFUND",
    "REFUND": "REFUND",
    "refund_policy": "REFUND",
    "PRIVACY_POLICY": "PRIVACY",
    "PRIVACY": "PRIVACY",
    "privacy_policy": "PRIVACY",
    "TERMS_OF_SERVICE": "TERMS",
    "TERMS": "TERMS",
    "terms_of_service": "TERMS",
    "SHIPPING_POLICY": "SHIPPING",
    "SHIPPING": "SHIPPING",
    "shipping_policy": "SHIPPING",
    "CONTACT_INFORMATION": "CONTACT",
    "CONTACT": "CONTACT",
    "contact_information": "CONTACT",
    "LEGAL_NOTICE": "LEGAL",
    "LEGAL": "LEGAL",
    "legal_notice": "LEGAL",
}

# In-memory Tier 1 Policy Cache: store_profile_id -> (timestamp, policies_dict)
_memory_policy_cache: dict[uuid.UUID, tuple[float, dict[str, StorePolicyItem]]] = {}
POLICY_CACHE_TTL_SECONDS = 300.0  # 5 minutes (Invariant R-17)


class ShopifyService:
    """Service orchestrating Shopify interactions over SOCKS5 proxy."""

    @staticmethod
    def normalize_email(email: str) -> str:
        """INVARIANT R-09: Strictly strip and lowercase only.

        Do not strip dots or sub-addressing (+tags) and do not merge distinct emails.
        """
        return email.strip().lower()

    @staticmethod
    def classify_order_flags(
        orders: list[dict[str, Any]],
        window_days: int = 60,
        reference_time: datetime.datetime | None = None,
    ) -> OrderClassificationFlags:
        """Calculates 5 independent boolean status flags according to Invariant R-10.

        Preserves customer relationship (has_order_record = True) even when cancelled or refunded.
        """
        ref_time = reference_time or datetime.datetime.now(datetime.UTC)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=datetime.UTC)

        window_start = ref_time - datetime.timedelta(days=window_days)

        # 1. Filter out test orders
        non_test_orders = [o for o in orders if o.get("test") is not True]

        # 2. Filter orders within 60-day window
        recent_orders: list[dict[str, Any]] = []
        for o in non_test_orders:
            created_at_raw = o.get("created_at")
            if not created_at_raw:
                continue
            if isinstance(created_at_raw, datetime.datetime):
                created_dt = created_at_raw
            else:
                created_dt = datetime.datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=datetime.UTC)

            if created_dt >= window_start:
                recent_orders.append(o)

        has_order_record = len(non_test_orders) > 0
        has_recent_order = len(recent_orders) > 0
        recent_orders_count = len(recent_orders)

        has_paid = False
        has_active = False
        has_cancelled = False
        has_refunded = False
        has_fulfilled = False

        # Classify flags based on orders
        eval_orders = recent_orders if recent_orders else non_test_orders
        for o in eval_orders:
            fin_status = (o.get("financial_status") or "").lower()
            ful_status = (o.get("fulfillment_status") or "").lower()
            cancelled_at = o.get("cancelled_at")
            is_cancelled = bool(cancelled_at) or (fin_status == "cancelled")

            if fin_status in ("paid", "partially_refunded"):
                has_paid = True

            if is_cancelled:
                has_cancelled = True

            if fin_status in ("refunded", "partially_refunded"):
                has_refunded = True

            if ful_status == "fulfilled":
                has_fulfilled = True

            # Active order: Valid financial status, not cancelled, not fully refunded, not yet fulfilled
            if (
                not is_cancelled
                and ful_status != "fulfilled"
                and fin_status != "refunded"
                and fin_status in ("paid", "partially_paid", "pending", "authorized", "partially_refunded")
            ):
                has_active = True

        return OrderClassificationFlags(
            has_order_record=has_order_record,
            has_recent_order=has_recent_order,
            recent_orders_count=recent_orders_count,
            has_paid_order=has_paid,
            has_active_order=has_active,
            has_cancelled_order=has_cancelled,
            has_refunded_order=has_refunded,
            has_fulfilled_order=has_fulfilled,
        )

    @staticmethod
    async def test_connection(
        shop_domain: str,
        client_id: str,
        client_secret: str,
        proxy_config: ResolvedProxyConfig,
    ) -> ShopifyConnectionTestResult:
        """Tests Shopify connection via SOCKS5 proxy using Client Credentials and GraphQL identity check."""
        t_start = time.perf_counter()
        token_mgr = ShopifyTokenManager(
            shop_domain=shop_domain,
            client_id=client_id,
            client_secret=client_secret,
            proxy_config=proxy_config,
        )

        try:
            # 1. Fetch initial token via proxy
            await token_mgr.get_access_token()

            # 2. Query shop identity via proxy GraphQL
            client = ProxyEnforcedShopifyClient(
                shop_domain=shop_domain,
                token_manager=token_mgr,
                proxy_config=proxy_config,
            )
            data = await client.execute_graphql(GRAPHQL_SHOP_IDENTITY)
            latency_ms = max(1, int((time.perf_counter() - t_start) * 1000))

            shop_data = data.get("data", {}).get("shop")
            if not shop_data:
                return ShopifyConnectionTestResult(
                    success=False,
                    error="SHOPIFY_IDENTITY_QUERY_FAILED",
                    detail=f"GraphQL response did not include shop data: {data}",
                    latency_ms=latency_ms,
                )

            # 3. Domain mismatch check
            resp_domain = shop_data.get("myshopifyDomain", "").lower()
            if resp_domain and resp_domain != shop_domain.lower():
                return ShopifyConnectionTestResult(
                    success=False,
                    error="STORE_DOMAIN_MISMATCH",
                    detail=f"Expected domain '{shop_domain}', but authenticated shop is '{resp_domain}'.",
                    latency_ms=latency_ms,
                )

            scopes = token_mgr.scopes or ["read_orders", "read_products", "read_all_policies"]

            return ShopifyConnectionTestResult(
                success=True,
                shop_name=shop_data.get("name", shop_domain),
                shop_domain=shop_domain,
                granted_scopes=scopes,
                token_expires_at=token_mgr.expires_at,
                latency_ms=latency_ms,
            )

        except ShopifyAuthError as exc:
            latency_ms = max(1, int((time.perf_counter() - t_start) * 1000))
            return ShopifyConnectionTestResult(
                success=False,
                error="SHOPIFY_AUTH_FAILED",
                detail=str(exc),
                latency_ms=latency_ms,
            )
        except ShopifyProxyError as exc:
            latency_ms = max(1, int((time.perf_counter() - t_start) * 1000))
            return ShopifyConnectionTestResult(
                success=False,
                error="PROXY_TUNNEL_FAILED",
                detail=str(exc),
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = max(1, int((time.perf_counter() - t_start) * 1000))
            return ShopifyConnectionTestResult(
                success=False,
                error="SHOPIFY_CONNECTION_ERROR",
                detail=str(exc),
                latency_ms=latency_ms,
            )

    @staticmethod
    async def lookup_recent_orders(
        client: ProxyEnforcedShopifyClient,
        customer_email: str,
        days: int = 60,
        reference_time: datetime.datetime | None = None,
    ) -> list[ShopifyOrderSummary]:
        """INVARIANT R-09: Looks up non-test orders created within the last 60 days via SOCKS5 proxy."""
        normalized_email = ShopifyService.normalize_email(customer_email)
        ref_time = reference_time or datetime.datetime.now(datetime.UTC)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=datetime.UTC)

        min_date = (ref_time - datetime.timedelta(days=days)).isoformat()

        params = {
            "status": "any",
            "created_at_min": min_date,
            "email": normalized_email,
            "test": "false",  # Exclude test orders at API level
            "limit": 50,
            "fields": (
                "id,name,email,created_at,cancelled_at,cancel_reason,"
                "financial_status,fulfillment_status,total_price,currency,test,line_items"
            ),
        }

        data = await client.execute_rest("orders.json", params=params)
        raw_orders = data.get("orders", [])

        # Defense-in-depth: Application-level non-test & time window filtering
        window_start = ref_time - datetime.timedelta(days=days)
        valid_orders: list[tuple[datetime.datetime, dict[str, Any]]] = []

        for o in raw_orders:
            if o.get("test") is True:
                continue
            created_str = o.get("created_at")
            if not created_str:
                continue
            created_dt = datetime.datetime.fromisoformat(str(created_str).replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=datetime.UTC)
            if created_dt >= window_start:
                valid_orders.append((created_dt, o))

        # Sort descending by creation time (most recent first)
        valid_orders.sort(key=lambda x: x[0], reverse=True)

        orders_summary: list[ShopifyOrderSummary] = []
        for created_dt, o in valid_orders:
            line_items = [
                ShopifyOrderLineItem(
                    id=li.get("id", ""),
                    title=li.get("title", ""),
                    quantity=li.get("quantity", 1),
                    price=li.get("price", "0.00"),
                )
                for li in o.get("line_items", [])
            ]
            orders_summary.append(
                ShopifyOrderSummary(
                    id=str(o.get("id", "")),
                    name=o.get("name", ""),
                    email=o.get("email", normalized_email),
                    created_at=created_dt,
                    financial_status=o.get("financial_status", "unknown"),
                    fulfillment_status=o.get("fulfillment_status"),
                    total_price=str(o.get("total_price", "0.00")),
                    currency=o.get("currency", "USD"),
                    line_items=line_items,
                )
            )

        return orders_summary

    @staticmethod
    async def enrich_order_lookup(
        client: ProxyEnforcedShopifyClient,
        customer_email: str,
        days: int = 60,
        reference_time: datetime.datetime | None = None,
    ) -> ShopifyOrderLookupResult:
        """Executes full order lookup and returns classified flags and summaries.

        INVARIANT R-11: Only returns 'no_order' on successful 200 OK with 0 matches.
        """
        normalized = ShopifyService.normalize_email(customer_email)
        orders = await ShopifyService.lookup_recent_orders(
            client=client,
            customer_email=normalized,
            days=days,
            reference_time=reference_time,
        )

        raw_dicts = [
            {
                "id": o.id,
                "name": o.name,
                "created_at": o.created_at,
                "financial_status": o.financial_status,
                "fulfillment_status": o.fulfillment_status,
                "test": False,
            }
            for o in orders
        ]
        flags = ShopifyService.classify_order_flags(
            orders=raw_dicts,
            window_days=days,
            reference_time=reference_time,
        )

        status_str = "success" if orders else "no_order"
        return ShopifyOrderLookupResult(
            customer_email=normalized,
            status=status_str,
            flags=flags,
            latest_order=orders[0] if orders else None,
            all_recent_orders=orders,
        )

    @staticmethod
    async def search_products_live(
        client: ProxyEnforcedShopifyClient,
        search_terms: list[str],
        max_results: int = 5,
    ) -> ProductSearchSnapshotData:
        """INVARIANT R-16: Live Product Search strictly via SOCKS5 proxy without Catalog Mirroring.

        Extracts Product and Variant facts. Emits PRODUCT_NOT_RESOLVED warning on 0 matches.
        """
        t_start = time.perf_counter()
        clean_terms = [re.sub(r'["\:\(\)]', "", term).strip() for term in search_terms if term.strip()]
        if not clean_terms:
            return ProductSearchSnapshotData(
                search_query="",
                raw_query_terms=[],
                matched_count=0,
                product_resolved=False,
                warning_codes=["PRODUCT_NOT_RESOLVED", "EMPTY_SEARCH_QUERY"],
                products=[],
                execution_time_ms=0,
            )

        title_conditions = " AND ".join(f"title:*{t}*" for t in clean_terms)
        query_str = f"status:active AND ({title_conditions})"

        variables = {
            "query": query_str,
            "first": max_results,
        }

        response_data = await client.execute_graphql(GRAPHQL_SEARCH_PRODUCTS, variables=variables)
        latency_ms = max(1, int((time.perf_counter() - t_start) * 1000))

        products_edges = response_data.get("data", {}).get("products", {}).get("edges", [])

        # Fallback to OR query if AND yields 0 results and multiple terms provided
        if not products_edges and len(clean_terms) > 1:
            relaxed_query = f"status:active AND ({' OR '.join(f'title:*{t}*' for t in clean_terms)})"
            fallback_resp = await client.execute_graphql(
                GRAPHQL_SEARCH_PRODUCTS, {"query": relaxed_query, "first": max_results}
            )
            products_edges = fallback_resp.get("data", {}).get("products", {}).get("edges", [])

        matched_products: list[ProductFact] = []
        for edge in products_edges:
            node = edge.get("node", {})
            if not node:
                continue

            variants: list[ProductVariantFact] = []
            variant_prices: list[float] = []
            has_in_stock = False

            for v_edge in node.get("variants", {}).get("edges", []):
                v_node = v_edge.get("node", {})
                price_str = str(v_node.get("price", "0.00"))
                try:
                    variant_prices.append(float(price_str))
                except ValueError:
                    variant_prices.append(0.0)

                is_available = bool(v_node.get("availableForSale", False))
                if is_available:
                    has_in_stock = True

                options_dict = {
                    opt.get("name", ""): opt.get("value", "")
                    for opt in v_node.get("selectedOptions", [])
                    if opt.get("name")
                }

                variants.append(
                    ProductVariantFact(
                        variant_id=str(v_node.get("id")),
                        title=v_node.get("title", "Default Title"),
                        sku=v_node.get("sku"),
                        price=price_str,
                        compare_at_price=v_node.get("compareAtPrice"),
                        currency="USD",
                        available_for_sale=is_available,
                        inventory_quantity=v_node.get("inventoryQuantity"),
                        options=options_dict,
                    )
                )

            min_p = f"{min(variant_prices):.2f}" if variant_prices else "0.00"
            max_p = f"{max(variant_prices):.2f}" if variant_prices else "0.00"

            raw_desc = node.get("description") or ""
            clean_desc = normalize_policy_text(raw_desc)
            desc_snippet = (clean_desc[:250] + "...") if len(clean_desc) > 250 else clean_desc

            feat_img = node.get("featuredImage")
            img_url = feat_img.get("url") if feat_img else None

            matched_products.append(
                ProductFact(
                    product_id=str(node.get("id")),
                    title=node.get("title", ""),
                    handle=node.get("handle", ""),
                    vendor=node.get("vendor"),
                    product_type=node.get("productType"),
                    status=node.get("status", "ACTIVE"),
                    description_snippet=desc_snippet,
                    online_store_url=node.get("onlineStoreUrl"),
                    featured_image_url=img_url,
                    total_inventory=node.get("totalInventory"),
                    min_price=min_p,
                    max_price=max_p,
                    has_in_stock_variant=has_in_stock,
                    variants=variants,
                )
            )

        matched_count = len(matched_products)
        product_resolved = matched_count > 0
        warning_codes: list[str] = []
        if not product_resolved:
            warning_codes.append("PRODUCT_NOT_RESOLVED")
        elif not any(p.has_in_stock_variant for p in matched_products):
            warning_codes.append("OUT_OF_STOCK")

        return ProductSearchSnapshotData(
            search_query=query_str,
            raw_query_terms=clean_terms,
            matched_count=matched_count,
            product_resolved=product_resolved,
            warning_codes=warning_codes,
            products=matched_products,
            execution_time_ms=latency_ms,
        )

    @staticmethod
    async def sync_legal_policies(client: ProxyEnforcedShopifyClient) -> ShopifyPoliciesSyncResult:
        """INVARIANT R-17: Fetches 6 standard policies from Shopify GraphQL via proxy.

        Sanitizes HTML, normalizes text, and calculates strict 64-hex SHA-256 hashes.
        """
        try:
            # 1. Try primary shopPolicies query
            data = await client.execute_graphql(GRAPHQL_POLICIES_PRIMARY)
            policies_list = data.get("data", {}).get("shop", {}).get("shopPolicies", [])

            policies: PolicyDict = PolicyDict()
            hashes: PolicyDict = PolicyDict()

            if policies_list:
                for item in policies_list:
                    raw_type = item.get("type", "")
                    canonical_key = POLICY_TYPE_MAP.get(raw_type)
                    if not canonical_key:
                        continue
                    raw_body = item.get("body") or ""
                    clean_text = normalize_policy_text(raw_body)
                    p_hash = compute_content_hash(clean_text)
                    policies[canonical_key] = clean_text
                    hashes[canonical_key] = p_hash
            else:
                # 2. Try fallback query for individual fields
                data_fb = await client.execute_graphql(GRAPHQL_POLICIES_FALLBACK)
                shop_fb = data_fb.get("data", {}).get("shop", {})
                fb_keys = [
                    ("refundPolicy", "REFUND"),
                    ("privacyPolicy", "PRIVACY"),
                    ("termsOfService", "TERMS"),
                    ("shippingPolicy", "SHIPPING"),
                    ("contactInformation", "CONTACT"),
                    ("legalNotice", "LEGAL"),
                ]
                for fb_field, c_key in fb_keys:
                    obj = shop_fb.get(fb_field)
                    raw_body = (obj.get("body") if obj else "") or ""
                    clean_text = normalize_policy_text(raw_body)
                    p_hash = compute_content_hash(clean_text)
                    policies[c_key] = clean_text
                    hashes[c_key] = p_hash

            # Ensure all 6 standard policies exist (empty string fallback with valid SHA-256)
            for c_key in CANONICAL_POLICY_TYPES:
                if c_key not in policies:
                    policies[c_key] = ""
                    hashes[c_key] = compute_content_hash("")

            return ShopifyPoliciesSyncResult(
                success=True,
                policies=policies,
                hashes=hashes,
            )
        except Exception as exc:
            return ShopifyPoliciesSyncResult(
                success=False,
                error=str(exc),
            )

    @staticmethod
    async def get_effective_store_policies(
        session: AsyncSession,
        store_profile_id: uuid.UUID,
        client: ProxyEnforcedShopifyClient | None = None,
        force_refresh: bool = False,
    ) -> dict[str, StorePolicyItem]:
        """INVARIANT R-17: Returns current policies using Two-Tier cache (In-memory 300s TTL + DB)."""
        now = time.time()
        now_utc = datetime.datetime.now(datetime.UTC)

        # 1. Check Tier 1 (In-memory cache)
        if not force_refresh and store_profile_id in _memory_policy_cache:
            cache_time, cached_items = _memory_policy_cache[store_profile_id]
            if now - cache_time < POLICY_CACHE_TTL_SECONDS:
                return cached_items

        # 2. Check Tier 2 (Database cache)
        stmt = select(StorePolicy).where(StorePolicy.store_profile_id == store_profile_id)
        existing_records = (await session.execute(stmt)).scalars().all()

        max_synced_at = max((r.synced_at for r in existing_records if not r.is_custom), default=None)
        db_is_fresh = (
            max_synced_at is not None
            and (now_utc - max_synced_at).total_seconds() < POLICY_CACHE_TTL_SECONDS
        )

        if not force_refresh and db_is_fresh and len(existing_records) >= 6:
            result = {
                r.policy_type: StorePolicyItem(
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
                for r in existing_records
            }
            _memory_policy_cache[store_profile_id] = (now, result)
            return result

        # 3. Cache expired or missing -> Sync from Shopify if client available
        if client:
            sync_res = await ShopifyService.sync_legal_policies(client)
            if sync_res.success:
                existing_map = {r.policy_type: r for r in existing_records}
                for p_type, clean_text in sync_res.policies.items():
                    p_hash = sync_res.hashes[p_type]
                    if p_type in existing_map and not existing_map[p_type].is_custom:
                        rec = existing_map[p_type]
                        rec.body_text = clean_text
                        rec.content_hash = p_hash
                        rec.synced_at = now_utc
                        rec.updated_at = now_utc
                    else:
                        title_display = f"{p_type.capitalize()} Policy"
                        new_rec = StorePolicy(
                            id=uuid.uuid4(),
                            store_profile_id=store_profile_id,
                            policy_type=p_type,
                            title=title_display,
                            body_text=clean_text,
                            content_hash=p_hash,
                            is_custom=False,
                            synced_at=now_utc,
                        )
                        session.add(new_rec)
                await session.commit()

                # Re-query
                updated_records = (await session.execute(stmt)).scalars().all()
                result = {
                    r.policy_type: StorePolicyItem(
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
                    for r in updated_records
                }
                _memory_policy_cache[store_profile_id] = (now, result)
                return result

        # If no client or sync failed, fallback to existing DB records
        result = {
            r.policy_type: StorePolicyItem(
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
            for r in existing_records
        }
        _memory_policy_cache[store_profile_id] = (now, result)
        return result

    @staticmethod
    def check_draft_policy_freshness(
        policy_hashes_used: dict[str, str] | None,
        current_policy_hashes: dict[str, str],
    ) -> StaleDraftCheckResult:
        """INVARIANT R-18: Checks whether any store policy changed since draft creation."""
        if not policy_hashes_used:
            return StaleDraftCheckResult(is_stale=False)

        stale_details: list[dict[str, Any]] = []
        for p_type, used_hash in policy_hashes_used.items():
            curr_hash = current_policy_hashes.get(p_type)
            if curr_hash and curr_hash != used_hash:
                stale_details.append(
                    {
                        "policy_type": p_type,
                        "used_hash": used_hash,
                        "current_hash": curr_hash,
                    }
                )

        if stale_details:
            return StaleDraftCheckResult(
                is_stale=True,
                stale_reason="POLICY_UPDATED",
                stale_details=stale_details,
            )
        return StaleDraftCheckResult(is_stale=False)
