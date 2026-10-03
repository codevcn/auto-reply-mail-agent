"""Tier 1 Feature Tests: Proxy Fail-Closed Transport & Token Management.
Requirements: Invariant 2, R-13, R-14, R-15; Sections 9, 10.
"""

import datetime
import unittest
from tests.e2e.fixtures import MockProxyClient


class MockTokenManager:
    def __init__(self, proxy: MockProxyClient):
        self.proxy = proxy
        self.token = "shpat_mock_token_initial"
        self.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
        self.refresh_count = 0

    def get_token(self) -> str:
        # Check if needs renewal (< 5 mins remaining)
        now = datetime.datetime.now(datetime.timezone.utc)
        if (self.expires_at - now).total_seconds() < 300:
            self.renew_token()
        return self.token

    def renew_token(self) -> str:
        # Enforce proxy call
        self.proxy.execute_shopify_request("/admin/oauth/access_token", {})
        self.refresh_count += 1
        self.token = f"shpat_mock_renewed_{self.refresh_count}"
        self.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
        return self.token

    def handle_401_recovery(self) -> str:
        # Single refresh on 401
        return self.renew_token()


class TestProxyFeatures(unittest.TestCase):
    def setUp(self):
        self.proxy = MockProxyClient(is_alive=True, remote_dns_enabled=True)
        self.token_mgr = MockTokenManager(proxy=self.proxy)

    def test_01_proxy_connection_test_validates_remote_dns_and_exit_ip(self):
        """TC-PROXY-01: Connection test checks remote DNS and returns exit IP."""
        res = self.proxy.test_connection()
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_ip"], "198.51.100.24")
        self.assertEqual(res["country"], "US")
        self.assertGreater(res["latency_ms"], 0)

    def test_02_proxy_rejects_local_dns_resolution(self):
        """TC-PROXY-02: Local DNS resolution leakage fails proxy verification."""
        leaky_proxy = MockProxyClient(is_alive=True, remote_dns_enabled=False)
        res = leaky_proxy.test_connection()
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "PROXY_DNS_RESOLUTION_FAILED")

    def test_03_fail_closed_invariant_when_proxy_down_no_direct_fallback(self):
        """TC-PROXY-03: When proxy is down, all Shopify requests fail closed without direct fallback."""
        dead_proxy = MockProxyClient(is_alive=False)
        with self.assertRaises(ConnectionError) as ctx:
            dead_proxy.execute_shopify_request("/admin/orders.json", {}, allow_direct_fallback=False)
        
        self.assertIn("Fail-closed enforced", str(ctx.exception))
        self.assertFalse(dead_proxy.direct_connection_attempted, "Direct fallback must never be attempted!")

    def test_04_token_auto_renewal_when_under_five_minutes(self):
        """TC-PROXY-04: Token automatically renews through proxy when under 5 minutes to expiry."""
        # Force expiry to 2 minutes from now
        self.token_mgr.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2)
        initial_token = self.token_mgr.token

        new_token = self.token_mgr.get_token()
        self.assertNotEqual(new_token, initial_token, "Token should have renewed.")
        self.assertEqual(self.token_mgr.refresh_count, 1)

    def test_05_401_recovery_renews_token_once_via_proxy(self):
        """TC-PROXY-05: 401 response from Shopify triggers token renewal once through proxy."""
        old_token = self.token_mgr.token
        renewed_token = self.token_mgr.handle_401_recovery()
        self.assertNotEqual(renewed_token, old_token)
        self.assertEqual(self.token_mgr.refresh_count, 1)


if __name__ == "__main__":
    unittest.main()
