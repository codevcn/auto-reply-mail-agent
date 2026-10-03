"""Shopify subsystem with proxy-enforced transport."""

from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyDomainMismatchError,
    ShopifyError,
    ShopifyPermissionError,
    ShopifyProxyError,
    ShopifyRateLimitError,
    ShopifyTransientError,
)
from app.shopify.router import shopify_router
from app.shopify.schemas import (
    ShopifyCandidateTestRequest,
    ShopifyConnectionTestResult,
    ShopifyOrderLineItem,
    ShopifyOrderSummary,
    ShopifyPoliciesSyncResult,
    ShopifyProductSummary,
)
from app.shopify.service import ShopifyService
from app.shopify.token import ShopifyTokenManager

__all__ = [
    "ProxyEnforcedShopifyClient",
    "ShopifyAuthError",
    "ShopifyCandidateTestRequest",
    "ShopifyConnectionTestResult",
    "ShopifyDomainMismatchError",
    "ShopifyError",
    "ShopifyOrderLineItem",
    "ShopifyOrderSummary",
    "ShopifyPermissionError",
    "ShopifyPoliciesSyncResult",
    "ShopifyProductSummary",
    "ShopifyProxyError",
    "ShopifyRateLimitError",
    "ShopifyService",
    "ShopifyTokenManager",
    "ShopifyTransientError",
    "shopify_router",
]
