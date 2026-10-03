"""SOCKS5 proxy management & fail-closed transport."""

from app.proxy.client import ProxyHealthChecker, ResolvedProxyConfig
from app.proxy.exceptions import (
    DNSLeakDetectedError,
    ProxyConfigurationError,
    ProxyConnectionError,
    ProxyError,
)
from app.proxy.router import proxy_router
from app.proxy.schemas import (
    ProxyCandidateTestRequest,
    ProxyProfileCreate,
    ProxyProfileResponse,
    ProxyProfileUpdate,
    ProxyTestResult,
)
from app.proxy.service import ProxyService
from app.proxy.transport import create_proxy_enforced_client

__all__ = [
    "DNSLeakDetectedError",
    "ProxyCandidateTestRequest",
    "ProxyConfigurationError",
    "ProxyConnectionError",
    "ProxyError",
    "ProxyHealthChecker",
    "ProxyProfileCreate",
    "ProxyProfileResponse",
    "ProxyProfileUpdate",
    "ProxyService",
    "ProxyTestResult",
    "ResolvedProxyConfig",
    "create_proxy_enforced_client",
    "proxy_router",
]
