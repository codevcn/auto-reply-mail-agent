"""Unit and integration tests for SOCKS5 fail-closed proxy transport and remote DNS."""

import pytest
from app.proxy.client import ProxyHealthChecker, ResolvedProxyConfig
from app.proxy.exceptions import (
    ProxyConfigurationError,
    ProxyConnectionError,
)
from app.proxy.transport import create_proxy_enforced_client


def test_resolved_proxy_url_format_enforces_socks5h():
    config = ResolvedProxyConfig(
        host="198.51.100.24",
        port=1080,
        username="user@detect",
        password="pass#secret:123",
        protocol="socks5",
    )
    url = config.get_proxy_url()
    assert url.startswith("socks5h://")
    assert "user%40detect" in url
    assert "pass%23secret%3A123" in url
    assert "198.51.100.24:1080" in url

    # Safe display masks password
    safe = config.to_safe_display()
    assert "pass" not in safe
    assert "***" in safe


@pytest.mark.asyncio
async def test_proxy_health_check_validates_remote_dns_and_exit_ip():
    config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    mock_res = {
        "success": True,
        "exit_ip": "198.51.100.24",
        "country": "US",
        "latency_ms": 115,
    }
    result = await ProxyHealthChecker.test_proxy(
        config, remote_dns_enabled=True, mock_response=mock_res
    )
    assert result.success is True
    assert result.exit_ip == "198.51.100.24"
    assert result.country == "US"
    assert result.remote_dns_active is True
    assert result.latency_ms == 115


@pytest.mark.asyncio
async def test_proxy_rejects_local_dns_leakage():
    config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    result = await ProxyHealthChecker.test_proxy(config, remote_dns_enabled=False)
    assert result.success is False
    assert result.error == "PROXY_DNS_RESOLUTION_FAILED"
    assert result.remote_dns_active is False


def test_fail_closed_invariant_when_no_proxy_configured():
    with pytest.raises(ProxyConfigurationError) as exc_info:
        create_proxy_enforced_client(None)

    assert "Fail-closed invariant enforced" in str(exc_info.value)
    assert issubclass(ProxyConnectionError, ConnectionError)


def test_prohibit_direct_fallback():
    config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    with pytest.raises(ProxyConfigurationError) as exc_info:
        create_proxy_enforced_client(config, allow_direct_fallback=True)

    assert "allow_direct_fallback is strictly prohibited" in str(exc_info.value)
