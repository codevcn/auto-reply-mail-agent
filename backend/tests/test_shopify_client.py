"""Unit and integration tests for Shopify token management and proxy-enforced client."""

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from app.proxy.client import ResolvedProxyConfig
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import ShopifyTransientError
from app.shopify.service import ShopifyService
from app.shopify.token import ShopifyTokenManager


@pytest.mark.asyncio
async def test_token_auto_renewal_when_under_five_minutes():
    """TC-PROXY-04: Token automatically renews through proxy when under 5 minutes to expiry."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    token_mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="id1",
        client_secret="secret1",
        proxy_config=proxy_config,
        initial_token="shpat_initial_valid_token",
        initial_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=2),  # < 5 min
    )

    with patch.object(token_mgr, "renew_token", new_callable=AsyncMock) as mock_renew:
        mock_renew.side_effect = lambda: setattr(token_mgr, "token", "shpat_renewed_token_1") or "shpat_renewed_token_1"
        token = await token_mgr.get_access_token()
        assert mock_renew.await_count == 1
        assert token == "shpat_renewed_token_1"


@pytest.mark.asyncio
async def test_401_recovery_renews_token_once():
    """TC-PROXY-05: 401 response from Shopify triggers token renewal once through proxy."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    token_mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="id1",
        client_secret="secret1",
        proxy_config=proxy_config,
        initial_token="shpat_stale_token",
    )

    with patch.object(token_mgr, "renew_token", new_callable=AsyncMock) as mock_renew:
        mock_renew.return_value = "shpat_recovered_token_2"
        recovered = await token_mgr.handle_401_recovery()
        assert recovered == "shpat_recovered_token_2"
        assert mock_renew.await_count == 1


@pytest.mark.asyncio
async def test_transient_error_does_not_swallow_as_no_order():
    """TC-SHOPIFY-04: 500 error from Shopify raises ShopifyTransientError, never silent no_order."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    token_mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="id1",
        client_secret="secret1",
        proxy_config=proxy_config,
        initial_token="shpat_token",
        initial_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    )
    client = ProxyEnforcedShopifyClient(
        shop_domain="wrydeco.myshopify.com",
        token_manager=token_mgr,
        proxy_config=proxy_config,
    )

    # Mock execute_rest to raise ShopifyTransientError on 500
    with patch.object(client, "execute_rest", new_callable=AsyncMock) as mock_rest:
        mock_rest.side_effect = ShopifyTransientError("Shopify internal 500 error")
        with pytest.raises(ShopifyTransientError):
            await ShopifyService.lookup_recent_orders(client, customer_email="customer@example.com")


@pytest.mark.asyncio
async def test_sync_six_policies_with_sha256():
    """TC-SHOPIFY-05: Six policies synced via GraphQL and SHA-256 hashes generated."""
    proxy_config = ResolvedProxyConfig(host="198.51.100.24", port=1080)
    token_mgr = ShopifyTokenManager(
        shop_domain="wrydeco.myshopify.com",
        client_id="id1",
        client_secret="secret1",
        proxy_config=proxy_config,
        initial_token="shpat_token",
        initial_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    )
    client = ProxyEnforcedShopifyClient(
        shop_domain="wrydeco.myshopify.com",
        token_manager=token_mgr,
        proxy_config=proxy_config,
    )

    mock_gql_data = {
        "data": {
            "shop": {
                "refundPolicy": {"body": "30-day refund window with receipt."},
                "privacyPolicy": {"body": "We protect your data."},
                "termsOfService": {"body": "Terms and conditions apply."},
                "shippingPolicy": {"body": "Standard shipping 3-5 days."},
                "contactInformation": {"body": "support@wrydeco.com"},
                "legalNotice": {"body": "Wrydeco Living Inc."},
            }
        }
    }

    with patch.object(client, "execute_graphql", new_callable=AsyncMock) as mock_gql:
        mock_gql.return_value = mock_gql_data
        result = await ShopifyService.sync_legal_policies(client)
        assert result.success is True
        assert len(result.policies) == 6
        assert len(result.hashes) == 6
        assert result.policies["refund_policy"] == "30-day refund window with receipt."
        assert len(result.hashes["refund_policy"]) == 64  # SHA-256 hex length
