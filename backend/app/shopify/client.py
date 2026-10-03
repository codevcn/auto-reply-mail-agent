"""Proxy-enforced Shopify Admin API client for GraphQL and REST."""

from __future__ import annotations

from typing import Any

from app.proxy.client import ResolvedProxyConfig
from app.proxy.exceptions import ProxyConfigurationError, ProxyConnectionError
from app.proxy.transport import create_proxy_enforced_client
from app.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyPermissionError,
    ShopifyProxyError,
    ShopifyRateLimitError,
    ShopifyTransientError,
)
from app.shopify.token import ShopifyTokenManager


class ProxyEnforcedShopifyClient:
    """Client for Shopify Admin API, strictly enforcing SOCKS5 proxy routing and auto token handling."""

    def __init__(
        self,
        shop_domain: str,
        token_manager: ShopifyTokenManager,
        proxy_config: ResolvedProxyConfig,
        api_version: str = "2024-07",
    ) -> None:
        self.shop_domain = shop_domain
        self.token_manager = token_manager
        self.proxy_config = proxy_config
        self.api_version = api_version

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def execute_graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executes GraphQL query/mutation strictly via proxy with 401 auto-recovery."""
        token = await self.token_manager.get_access_token()
        endpoint = f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(2):  # Max 1 retry for 401
            try:
                async with create_proxy_enforced_client(self.proxy_config, timeout=30.0) as client:
                    resp = await client.post(endpoint, json=payload, headers=self._headers(token))
            except (ProxyConfigurationError, ProxyConnectionError) as exc:
                raise ShopifyProxyError(f"Fail-closed: Proxy tunnel failed: {exc}") from exc
            except Exception as exc:
                raise ShopifyProxyError(f"Network error via proxy: {exc}") from exc

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                if attempt == 0:
                    token = await self.token_manager.handle_401_recovery()
                    continue
                raise ShopifyAuthError(f"401 Unauthorized after token recovery attempt: {resp.text}")
            elif resp.status_code == 403:
                raise ShopifyPermissionError(f"403 Forbidden: Missing required scopes ({resp.text})")
            elif resp.status_code == 429:
                raise ShopifyRateLimitError("Shopify rate limit (429) exceeded")
            elif resp.status_code in (500, 502, 503, 504):
                # Transient error: do not silently swallow
                raise ShopifyTransientError(
                    f"Shopify transient error ({resp.status_code}): {resp.text}"
                )
            else:
                raise ShopifyTransientError(
                    f"Shopify error ({resp.status_code}): {resp.text}"
                )

        raise ShopifyAuthError("401 Unauthorized: token recovery exhausted.")

    async def execute_rest(
        self,
        endpoint: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executes REST Admin API request strictly via proxy with 401 recovery and transient retry."""
        token = await self.token_manager.get_access_token()
        url = f"https://{self.shop_domain}/admin/api/{self.api_version}/{endpoint.lstrip('/')}"

        for attempt in range(2):
            try:
                async with create_proxy_enforced_client(self.proxy_config, timeout=30.0) as client:
                    resp = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_data,
                        headers=self._headers(token),
                    )
            except (ProxyConfigurationError, ProxyConnectionError) as exc:
                raise ShopifyProxyError(f"Fail-closed: Proxy tunnel failed: {exc}") from exc
            except Exception as exc:
                raise ShopifyProxyError(f"Network error via proxy: {exc}") from exc

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                if attempt == 0:
                    token = await self.token_manager.handle_401_recovery()
                    continue
                raise ShopifyAuthError(f"401 Unauthorized after token recovery attempt: {resp.text}")
            elif resp.status_code == 403:
                raise ShopifyPermissionError(f"403 Forbidden: Missing required scopes ({resp.text})")
            elif resp.status_code == 429:
                raise ShopifyRateLimitError("Shopify rate limit (429) exceeded")
            elif resp.status_code in (500, 502, 503, 504):
                raise ShopifyTransientError(
                    f"Shopify transient server error ({resp.status_code}): {resp.text}"
                )
            else:
                raise ShopifyTransientError(
                    f"Shopify REST error ({resp.status_code}): {resp.text}"
                )

        raise ShopifyAuthError("401 Unauthorized: token recovery exhausted.")
