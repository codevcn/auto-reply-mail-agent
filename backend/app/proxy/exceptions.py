"""Proxy exceptions for SOCKS5 fail-closed transport."""

from __future__ import annotations


class ProxyError(Exception):
    """Base exception for proxy-related errors."""


class ProxyConfigurationError(ProxyError):
    """Raised when proxy configuration is missing or invalid."""


class ProxyConnectionError(ProxyError, ConnectionError):
    """Raised when connection through proxy fails. Inherits from ConnectionError for fail-closed checks."""


class DNSLeakDetectedError(ProxyError):
    """Raised when proxy connection allows local DNS resolution rather than remote DNS."""
