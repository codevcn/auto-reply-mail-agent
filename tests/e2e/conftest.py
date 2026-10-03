"""Pytest fixtures configuration for E2E Test Suite.
"""

from __future__ import annotations

import datetime
import pytest

from tests.e2e.fixtures import (
    MockAuthService,
    MockProxyClient,
    MockShopifyService,
    MockMailEngine,
)


@pytest.fixture
def auth_service():
    service = MockAuthService()
    service.bootstrap_admin("admin", "$argon2id$v=19$m=65536,t=3,p=4$dummyhash")
    return service


@pytest.fixture
def proxy_client():
    return MockProxyClient(is_alive=True, remote_dns_enabled=True)


@pytest.fixture
def shopify_service(proxy_client):
    return MockShopifyService(proxy=proxy_client)


@pytest.fixture
def mail_engine():
    return MockMailEngine()
