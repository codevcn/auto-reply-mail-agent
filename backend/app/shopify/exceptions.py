"""Shopify subsystem exceptions."""

from __future__ import annotations


class ShopifyError(Exception):
    """Base exception for Shopify API operations."""


class ShopifyAuthError(ShopifyError):
    """Raised when authentication with Shopify fails (e.g. invalid credentials or 401 retry exhausted)."""


class ShopifyProxyError(ShopifyError, ConnectionError):
    """Raised when request to Shopify fails due to proxy tunnel failure."""


class ShopifyPermissionError(ShopifyError):
    """Raised when Shopify returns 403 Forbidden due to missing required scopes."""


class ShopifyRateLimitError(ShopifyError):
    """Raised when Shopify API rate limit (429) is exceeded."""


class ShopifyTransientError(ShopifyError):
    """Raised when Shopify returns a transient 5xx server error. Must never be treated as no_order."""


class ShopifyDomainMismatchError(ShopifyError):
    """Raised when canonical myshopify domain does not match the authenticated shop identity."""
