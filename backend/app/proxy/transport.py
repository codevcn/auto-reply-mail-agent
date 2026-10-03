"""Proxy-enforced transport factory for fail-closed HTTP clients."""

from __future__ import annotations

import httpx

from app.proxy.client import ResolvedProxyConfig
from app.proxy.exceptions import ProxyConfigurationError, ProxyConnectionError


def create_proxy_enforced_client(
    proxy_config: ResolvedProxyConfig | None,
    timeout: float = 30.0,
    allow_direct_fallback: bool = False,
) -> httpx.AsyncClient:
    """Creates an httpx.AsyncClient strictly routed through the configured SOCKS5 proxy.

    Enforces Invariant R-02 (Fail-Closed):
    1. Direct connection fallback is strictly prohibited.
    2. Missing proxy configuration immediately raises ProxyConfigurationError.
    3. Remote DNS resolution is enforced via 'socks5h://'.
    4. HTTP redirects are disabled to avoid uninspected destination leaks.
    """
    if proxy_config is None:
        raise ProxyConfigurationError(
            "Fail-closed invariant enforced: Không có cấu hình proxy hoạt động. "
            "Tuyệt đối cấm kết nối trực tiếp đến Shopify API."
        )

    if allow_direct_fallback:
        raise ProxyConfigurationError(
            "Fail-closed invariant enforced: allow_direct_fallback is strictly prohibited."
        )

    proxy_url = proxy_config.get_proxy_url()
    client_timeout = httpx.Timeout(
        timeout=timeout,
        connect=float(proxy_config.connect_timeout),
        read=timeout,
        write=timeout,
        pool=float(proxy_config.connect_timeout),
    )

    try:
        client = httpx.AsyncClient(
            proxy=proxy_url,
            timeout=client_timeout,
            follow_redirects=False,
            verify=True,
        )
        return client
    except Exception as exc:
        raise ProxyConnectionError(
            f"Fail-closed enforced: Không thể khởi tạo đường hầm SOCKS5: {exc}"
        ) from exc
