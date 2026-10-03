"""Adversarial stress testing for Invariant R-02 (Fail-Closed Proxy) and Shopify Token Management.

Designed and executed by empirical challenger (challenger_phase2_1).
Verifies:
1. Dead / unreachable proxy triggers fail-closed error with ZERO bytes sent to destination.
2. Hanging / timing out proxy fails closed without fallback.
3. Transport rejects direct fallback flags and missing proxy config.
4. Remote DNS scheme socks5h:// enforcement prevents host VPS DNS leakage.
5. Token auto-refresh strictly adheres to < 300s window.
6. Concurrent token refresh calls are serialized (no thundering herd).
7. 401 Unauthorized single retry recovery and infinite retry loop prevention.
8. HTTP redirects are strictly disabled (follow_redirects=False).
9. Transient 5xx errors are never silently swallowed.
"""

import asyncio
import datetime
import socket
import sys
import threading
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.proxy.client import ProxyHealthChecker, ResolvedProxyConfig
from app.proxy.exceptions import (
    ProxyConfigurationError,
    ProxyConnectionError,
)
from app.proxy.transport import create_proxy_enforced_client
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyProxyError,
    ShopifyTransientError,
)
from app.shopify.token import ShopifyTokenManager

# ---------------------------------------------------------------------------
# Test Helpers: Local Socket Spy Servers to detect any direct connection leak
# ---------------------------------------------------------------------------


class SocketTrafficSpy:
    """A raw TCP socket server that listens on localhost and records any inbound connection/traffic."""

    def __init__(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.port: int = self.server_socket.getsockname()[1]
        self.server_socket.listen(5)
        self.connection_count = 0
        self.bytes_received = 0
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.server_socket.settimeout(0.2)
        while self._running:
            try:
                conn, _ = self.server_socket.accept()
                self.connection_count += 1
                try:
                    data = conn.recv(4096)
                    self.bytes_received += len(data)
                finally:
                    conn.close()
            except TimeoutError:
                continue
            except Exception:
                break

    def close(self) -> None:
        self._running = False
        self.server_socket.close()
        self._thread.join(timeout=1.0)


class HangingProxySimulator:
    """A mock proxy server that accepts TCP connection but hangs indefinitely without SOCKS5 handshake reply."""

    def __init__(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.port: int = self.server_socket.getsockname()[1]
        self.server_socket.listen(5)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.server_socket.settimeout(0.2)
        while self._running:
            try:
                conn, _ = self.server_socket.accept()
                # Accept connection but do not send anything back, simulate proxy hang/unresponsiveness
                while self._running:
                    threading.Event().wait(0.05)
                conn.close()
            except TimeoutError:
                continue
            except Exception:
                break

    def close(self) -> None:
        self._running = False
        self.server_socket.close()
        self._thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# ADVERSARIAL TEST CASES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adv_01_dead_proxy_fails_closed_zero_bytes_to_target():
    """Adversarial 1: When proxy is dead/unreachable, client fails closed and leaks 0 BYTES to direct target."""
    target_spy = SocketTrafficSpy()
    # Find an unused port that definitely refuses connections for proxy
    dead_proxy_port = 59998
    dead_proxy_config = ResolvedProxyConfig(
        host="127.0.0.1",
        port=dead_proxy_port,
        connect_timeout=1,
    )

    try:
        # 1. Attempt raw HTTP request through dead proxy towards the target spy
        client = create_proxy_enforced_client(dead_proxy_config, timeout=2.0)
        with pytest.raises(Exception) as exc_info:
            async with client:
                await client.get(f"http://127.0.0.1:{target_spy.port}/admin/orders.json")

        # Verify exception is a network/proxy connection error
        assert isinstance(
            exc_info.value,
            (
                httpx.ProxyError,
                httpx.ConnectError,
                httpx.TimeoutException,
                ConnectionError,
                ShopifyProxyError,
            ),
        )

        # EMPIRICAL ORACLE: Target server must have received exactly 0 connections and 0 bytes!
        assert target_spy.connection_count == 0, (
            "Direct connection attempt detected! Invariant R-02 violated!"
        )
        assert target_spy.bytes_received == 0, (
            "Data bytes leaked directly to target! Invariant R-02 violated!"
        )

        # 2. Verify ShopifyTokenManager fails closed with ShopifyProxyError (which is a ConnectionError)
        token_mgr = ShopifyTokenManager(
            shop_domain=f"127.0.0.1:{target_spy.port}",
            client_id="dummy_id",
            client_secret="dummy_secret",
            proxy_config=dead_proxy_config,
        )
        with pytest.raises(ShopifyProxyError):
            await token_mgr.renew_token()

        assert issubclass(ShopifyProxyError, ConnectionError)
        assert target_spy.connection_count == 0
        assert target_spy.bytes_received == 0

        # 3. Verify ProxyEnforcedShopifyClient fails closed with ShopifyProxyError
        shopify_client = ProxyEnforcedShopifyClient(
            shop_domain=f"127.0.0.1:{target_spy.port}",
            token_manager=token_mgr,
            proxy_config=dead_proxy_config,
        )
        with pytest.raises(ShopifyProxyError):
            await shopify_client.execute_rest("/orders.json")

        assert target_spy.connection_count == 0
        assert target_spy.bytes_received == 0

    finally:
        target_spy.close()


@pytest.mark.asyncio
async def test_adv_02_hanging_proxy_fails_closed_zero_bytes_to_target():
    """Adversarial 2: When proxy hangs/times out during handshake, client times out and leaks 0 bytes."""
    hanging_proxy = HangingProxySimulator()
    target_spy = SocketTrafficSpy()

    proxy_config = ResolvedProxyConfig(
        host="127.0.0.1",
        port=hanging_proxy.port,
        connect_timeout=1,
    )

    try:
        client = create_proxy_enforced_client(proxy_config, timeout=1.0)
        with pytest.raises(
            (httpx.TimeoutException, httpx.ProxyError, ConnectionError, TimeoutError)
        ) as exc_info:
            async with client:
                await asyncio.wait_for(
                    client.get(f"http://127.0.0.1:{target_spy.port}/admin/shop.json"),
                    timeout=2.0,
                )

        assert isinstance(
            exc_info.value,
            (httpx.TimeoutException, httpx.ProxyError, ConnectionError, TimeoutError),
        )
        # Ensure no fallback was attempted
        assert target_spy.connection_count == 0
        assert target_spy.bytes_received == 0

    finally:
        hanging_proxy.close()
        target_spy.close()


def test_adv_03_reject_direct_fallback_flags_and_missing_config():
    """Adversarial 3: Transport must strictly refuse direct fallback flags and missing proxy configs."""
    valid_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)

    # 1. Flag allow_direct_fallback=True must be rejected with ProxyConfigurationError
    with pytest.raises(ProxyConfigurationError) as exc_fallback:
        create_proxy_enforced_client(valid_config, allow_direct_fallback=True)
    assert "allow_direct_fallback is strictly prohibited" in str(exc_fallback.value)

    # 2. None proxy_config must be rejected
    with pytest.raises(ProxyConfigurationError) as exc_none:
        create_proxy_enforced_client(None)
    assert "Fail-closed invariant enforced" in str(exc_none.value)

    # 3. Inheritance checks for Invariant R-02
    assert issubclass(ProxyConnectionError, ConnectionError)
    assert issubclass(ShopifyProxyError, ConnectionError)


def test_adv_04_remote_dns_scheme_enforcement_and_special_character_quoting():
    """Adversarial 4: Verify socks5h:// URL scheme is mandatory and credentials are safely URL-encoded."""
    # 1. Scheme must be socks5h://, NEVER socks5:// (to prevent local DNS leakage on host)
    cfg_socks5 = ResolvedProxyConfig(host="proxy.internal.net", port=1080, protocol="socks5")
    url = cfg_socks5.get_proxy_url()
    assert url.startswith("socks5h://"), f"Vulnerable scheme detected: {url}. Must be socks5h://"
    assert not url.startswith("socks5://"), "socks5:// allows local DNS leak!"

    # Case-insensitive protocol handling
    cfg_socks5_upper = ResolvedProxyConfig(host="proxy.internal.net", port=1080, protocol="SOCKS5")
    assert cfg_socks5_upper.get_proxy_url().startswith("socks5h://")

    # 2. Special characters in username/password must be escaped so they don't break proxy URL structure
    cfg_special = ResolvedProxyConfig(
        host="198.51.100.24",
        port=1080,
        username="user@shop:tenant#1",
        password="p@ss/word#with?special&chars",
    )
    url_special = cfg_special.get_proxy_url()
    assert "user%40shop%3Atenant%231" in url_special
    assert "p%40ss%2Fword%23with%3Fspecial%26chars" in url_special
    assert "@198.51.100.24:1080" in url_special

    # 3. Safe display must NEVER reveal password
    safe_display = cfg_special.to_safe_display()
    assert "special" not in safe_display
    assert "***" in safe_display


@pytest.mark.asyncio
async def test_adv_05_remote_dns_health_checker_rejects_leakage():
    """Adversarial 5: ProxyHealthChecker must fail if remote DNS resolution is detected as disabled."""
    cfg = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    res = await ProxyHealthChecker.test_proxy(cfg, remote_dns_enabled=False)
    assert res.success is False
    assert res.error == "PROXY_DNS_RESOLUTION_FAILED"
    assert res.remote_dns_active is False


@pytest.mark.asyncio
async def test_adv_06_token_auto_refresh_threshold_boundaries():
    """Adversarial 6: Stress-test boundary conditions for 300s token renewal window."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    now = datetime.datetime.now(datetime.UTC)

    # Sub-case A: 305 seconds remaining (> 300s) -> DO NOT renew
    mgr_fresh = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token="shpat_valid_fresh",
        initial_expires_at=now + datetime.timedelta(seconds=305),
    )
    with patch.object(mgr_fresh, "renew_token", new_callable=AsyncMock) as mock_renew:
        token = await mgr_fresh.get_access_token()
        assert token == "shpat_valid_fresh"
        assert mock_renew.await_count == 0, "Token renewed prematurely when > 300s remained!"

    # Sub-case B: 299 seconds remaining (< 300s) -> MUST renew
    mgr_expiring = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token="shpat_almost_expired",
        initial_expires_at=now + datetime.timedelta(seconds=299),
    )
    with patch.object(mgr_expiring, "renew_token", new_callable=AsyncMock) as mock_renew:
        mock_renew.side_effect = (
            lambda: setattr(mgr_expiring, "token", "shpat_renewed_boundary")
            or "shpat_renewed_boundary"
        )
        token = await mgr_expiring.get_access_token()
        assert token == "shpat_renewed_boundary"
        assert mock_renew.await_count == 1, "Token failed to renew when within 300s window!"

    # Sub-case C: Expired in the past (-10 seconds) -> MUST renew
    mgr_expired = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token="shpat_expired_in_past",
        initial_expires_at=now - datetime.timedelta(seconds=10),
    )
    with patch.object(mgr_expired, "renew_token", new_callable=AsyncMock) as mock_renew:
        mock_renew.side_effect = (
            lambda: setattr(mgr_expired, "token", "shpat_renewed_expired")
            or "shpat_renewed_expired"
        )
        token = await mgr_expired.get_access_token()
        assert token == "shpat_renewed_expired"
        assert mock_renew.await_count == 1

    # Sub-case D: None token or None expires_at -> MUST renew
    mgr_none = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token=None,
        initial_expires_at=None,
    )
    with patch.object(mgr_none, "renew_token", new_callable=AsyncMock) as mock_renew:
        mock_renew.side_effect = (
            lambda: setattr(mgr_none, "token", "shpat_renewed_none") or "shpat_renewed_none"
        )
        token = await mgr_none.get_access_token()
        assert token == "shpat_renewed_none"
        assert mock_renew.await_count == 1


@pytest.mark.asyncio
async def test_adv_07_token_refresh_thundering_herd_concurrency():
    """Adversarial 7: 25 concurrent requests when token is expired must cause ONLY ONE renewal."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    now = datetime.datetime.now(datetime.UTC)

    mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token="shpat_old",
        initial_expires_at=now - datetime.timedelta(seconds=60),
    )

    actual_network_calls = 0

    async def fake_renew_token():
        nonlocal actual_network_calls
        async with mgr._lock:
            # Check double check lock
            cur_now = datetime.datetime.now(datetime.UTC)
            if (
                mgr.token != "shpat_old"
                and mgr.expires_at
                and (mgr.expires_at - cur_now).total_seconds() >= 300
            ):
                return mgr.token
            await asyncio.sleep(0.05)  # Simulate network latency
            actual_network_calls += 1
            mgr.token = "shpat_renewed_atomic"
            mgr.expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)
            return mgr.token

    with patch.object(mgr, "renew_token", side_effect=fake_renew_token):
        # Fire 25 concurrent coroutines calling get_access_token
        tasks = [mgr.get_access_token() for _ in range(25)]
        results = await asyncio.gather(*tasks)

        assert all(tok == "shpat_renewed_atomic" for tok in results)
        assert actual_network_calls == 1, (
            f"Thundering herd detected! Expected 1 call, got {actual_network_calls}"
        )


@pytest.mark.asyncio
async def test_adv_08_401_recovery_single_retry_and_infinite_loop_prevention():
    """Adversarial 8: 401 Unauthorized recovers at most ONCE; consecutive 401s MUST NOT loop infinitely."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    token_mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token="shpat_stale_token",
        initial_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    )
    client = ProxyEnforcedShopifyClient(
        shop_domain="wrydeco.myshopify.com",
        token_manager=token_mgr,
        proxy_config=proxy_config,
    )

    # Sub-case A: First call 401, second call 200 -> Successfully recovers in exactly 2 calls
    mock_response_401 = httpx.Response(status_code=401, text="Invalid API key or access token")
    mock_response_200 = httpx.Response(
        status_code=200, json={"data": {"shop": {"name": "Wrydeco"}}}
    )

    with patch.object(token_mgr, "handle_401_recovery", new_callable=AsyncMock) as mock_401_recovery:
        mock_401_recovery.return_value = "shpat_fresh_recovered"

        # Mocking transport client post responses: 401 then 200
        with patch("app.shopify.client.create_proxy_enforced_client") as mock_create_client:
            mock_http_client = AsyncMock()
            mock_http_client.post.side_effect = [mock_response_401, mock_response_200]
            mock_http_client.__aenter__.return_value = mock_http_client
            mock_http_client.__aexit__.return_value = None
            mock_create_client.return_value = mock_http_client

            result = await client.execute_graphql("{ shop { name } }")
            assert result["data"]["shop"]["name"] == "Wrydeco"
            assert mock_http_client.post.call_count == 2
            assert mock_401_recovery.await_count == 1

    # Sub-case B: Consecutive 401s (token renewal still rejected or credentials revoked)
    # MUST raise ShopifyAuthError and STOP at 2 requests. NO INFINITE LOOP!
    with patch.object(token_mgr, "handle_401_recovery", new_callable=AsyncMock) as mock_401_recovery:
        mock_401_recovery.return_value = "shpat_still_invalid"

        with patch("app.shopify.client.create_proxy_enforced_client") as mock_create_client:
            mock_http_client = AsyncMock()
            mock_http_client.post.side_effect = [
                mock_response_401,
                mock_response_401,
                mock_response_401,
            ]
            mock_http_client.__aenter__.return_value = mock_http_client
            mock_http_client.__aexit__.return_value = None
            mock_create_client.return_value = mock_http_client

            with pytest.raises(ShopifyAuthError) as exc_info:
                await client.execute_graphql("{ shop { name } }")

            assert "401 Unauthorized" in str(exc_info.value)
            # Verify exactly 2 calls were made, loop broke immediately on second 401
            assert mock_http_client.post.call_count == 2, (
                "Infinite retry detected! Must cap at 2 attempts."
            )
            assert mock_401_recovery.await_count == 1

    # Sub-case C: Same verification for REST execute_rest
    with patch.object(token_mgr, "handle_401_recovery", new_callable=AsyncMock) as mock_401_recovery:
        mock_401_recovery.return_value = "shpat_still_invalid"

        with patch("app.shopify.client.create_proxy_enforced_client") as mock_create_client:
            mock_http_client = AsyncMock()
            mock_http_client.request.side_effect = [mock_response_401, mock_response_401]
            mock_http_client.__aenter__.return_value = mock_http_client
            mock_http_client.__aexit__.return_value = None
            mock_create_client.return_value = mock_http_client

            with pytest.raises(ShopifyAuthError) as exc_info:
                await client.execute_rest("/orders.json")

            assert "401 Unauthorized" in str(exc_info.value)
            assert mock_http_client.request.call_count == 2
            assert mock_401_recovery.await_count == 1


def test_adv_09_follow_redirects_disabled_prevents_open_redirect_leak():
    """Adversarial 9: AsyncClient must have follow_redirects=False to prevent redirect leakage."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    client = create_proxy_enforced_client(proxy_config)
    # Check httpx internal attribute for follow_redirects
    assert client.follow_redirects is False, (
        "follow_redirects must be False to prevent uninspected redirect leaks!"
    )


@pytest.mark.asyncio
async def test_adv_10_transient_5xx_errors_never_swallowed():
    """Adversarial 10: 500, 502, 503, 504 server errors must raise ShopifyTransientError, never no_order."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    token_mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="cid",
        client_secret="csecret",
        proxy_config=proxy_config,
        initial_token="shpat_valid",
        initial_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    )
    client = ProxyEnforcedShopifyClient(
        shop_domain="wrydeco.myshopify.com",
        token_manager=token_mgr,
        proxy_config=proxy_config,
    )

    for status_code in (500, 502, 503, 504):
        mock_response = httpx.Response(
            status_code=status_code, text=f"Gateway / Server Error {status_code}"
        )
        with patch("app.shopify.client.create_proxy_enforced_client") as mock_create_client:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.__aenter__.return_value = mock_http
            mock_http.__aexit__.return_value = None
            mock_create_client.return_value = mock_http

            with pytest.raises(ShopifyTransientError) as exc_info:
                await client.execute_graphql("{ shop { name } }")

            assert str(status_code) in str(exc_info.value)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
