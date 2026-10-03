"""Shopify Access Token manager with automatic renewal and 401 recovery."""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

from app.core.redaction import register_secret
from app.proxy.client import ResolvedProxyConfig
from app.proxy.exceptions import ProxyConfigurationError, ProxyConnectionError
from app.proxy.transport import create_proxy_enforced_client
from app.shopify.exceptions import ShopifyAuthError, ShopifyProxyError


class ShopifyTokenManager:
    """Manages Shopify OAuth Access Token lifecycle via client credentials over SOCKS5 proxy."""

    def __init__(
        self,
        shop_domain: str,
        client_id: str,
        client_secret: str,
        proxy_config: ResolvedProxyConfig,
        initial_token: str | None = None,
        initial_expires_at: datetime.datetime | None = None,
    ) -> None:
        self.shop_domain = shop_domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.proxy_config = proxy_config
        self.token = initial_token
        self.expires_at = initial_expires_at
        self.refresh_count = 0
        self.scopes: list[str] = []
        self._lock = asyncio.Lock()

        # Immediately redact client secret and initial token
        register_secret(client_secret)
        if initial_token:
            register_secret(initial_token)

    async def get_access_token(self) -> str:
        """Returns valid access token, auto-renewing if expired or expires within 5 minutes (300s)."""
        now = datetime.datetime.now(datetime.UTC)
        needs_renewal = (
            self.token is None
            or self.expires_at is None
            or (self.expires_at - now).total_seconds() < 300
        )

        if needs_renewal:
            await self.renew_token()

        assert self.token is not None
        return self.token

    async def renew_token(self) -> str:
        """Requests new access token via Shopify Client Credentials Grant over enforced SOCKS5 proxy."""
        async with self._lock:
            # Check if another coroutine just renewed it
            now = datetime.datetime.now(datetime.UTC)
            if (
                self.token is not None
                and self.expires_at is not None
                and (self.expires_at - now).total_seconds() >= 300
            ):
                return self.token

            endpoint = f"https://{self.shop_domain}/admin/oauth/access_token"
            payload: dict[str, Any] = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            }

            try:
                async with create_proxy_enforced_client(self.proxy_config, timeout=20.0) as client:
                    resp = await client.post(endpoint, json=payload)
            except (ProxyConfigurationError, ProxyConnectionError) as exc:
                raise ShopifyProxyError(f"Fail-closed: Proxy error during token renewal: {exc}") from exc
            except Exception as exc:
                raise ShopifyProxyError(f"Network error during token renewal: {exc}") from exc

            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("access_token")
                expires_in = data.get("expires_in", 86400)
                self.expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                    seconds=expires_in
                )
                self.refresh_count += 1
                raw_scope = data.get("scope", "")
                if raw_scope:
                    self.scopes = [s.strip() for s in raw_scope.split(",") if s.strip()]

                if self.token:
                    register_secret(self.token)
                return self.token or ""
            elif resp.status_code in (400, 401):
                raise ShopifyAuthError(
                    f"Shopify authentication failed ({resp.status_code}): {resp.text}"
                )
            else:
                raise ShopifyAuthError(
                    f"Unexpected status from Shopify access_token ({resp.status_code}): {resp.text}"
                )

    async def handle_401_recovery(self) -> str:
        """Handles 401 Unauthorized by clearing cached token and requesting renewal once."""
        self.token = None
        self.expires_at = None
        return await self.renew_token()
