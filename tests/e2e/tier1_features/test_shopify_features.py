"""Tier 1 Feature Tests: Shopify Proxy Enrichment & Store Policies.
Requirements: R-09, R-10, R-11, R-16, R-17, R-18; Sections 10, 14.
"""

import datetime
import unittest
from tests.e2e.fixtures import (
    MockProxyClient,
    MockShopifyService,
    POLICY_TYPES,
)


class TestShopifyFeatures(unittest.TestCase):
    def setUp(self):
        self.proxy = MockProxyClient(is_alive=True, remote_dns_enabled=True)
        self.shopify = MockShopifyService(proxy=self.proxy)
        self.now = datetime.datetime.now(datetime.timezone.utc)

    def test_01_recent_order_within_60_days_matched(self):
        """TC-SHOPIFY-01: Order created 25 days ago matches 60-day window (R-09)."""
        self.shopify.orders_db.append({
            "id": "ord_101",
            "customer_email": "buyer@example.com",
            "created_at": self.now - datetime.timedelta(days=25),
            "financial_status": "paid",
        })

        res = self.shopify.get_order_by_customer_email("buyer@example.com", reference_time=self.now)
        self.assertTrue(res["has_order_record"])
        self.assertTrue(res["has_recent_order"])
        self.assertEqual(res["recent_orders_count"], 1)

    def test_02_older_order_beyond_60_days_excluded_from_recent(self):
        """TC-SHOPIFY-02: Order created 65 days ago has record but is NOT recent (R-09)."""
        self.shopify.orders_db.append({
            "id": "ord_102",
            "customer_email": "old_buyer@example.com",
            "created_at": self.now - datetime.timedelta(days=65),
            "financial_status": "paid",
        })

        res = self.shopify.get_order_by_customer_email("old_buyer@example.com", reference_time=self.now)
        self.assertTrue(res["has_order_record"], "Should recognize as prior customer.")
        self.assertFalse(res["has_recent_order"], "Must NOT be flagged as recent order.")
        self.assertEqual(res["recent_orders_count"], 0)

    def test_03_cancelled_and_refunded_orders_preserve_customer_record(self):
        """TC-SHOPIFY-03: Cancelled or refunded orders preserve customer flags (R-10)."""
        self.shopify.orders_db.append({
            "id": "ord_103",
            "customer_email": "refunded_buyer@example.com",
            "created_at": self.now - datetime.timedelta(days=10),
            "financial_status": "refunded",
        })

        res = self.shopify.get_order_by_customer_email("refunded_buyer@example.com", reference_time=self.now)
        self.assertTrue(res["has_order_record"])
        self.assertTrue(res["has_refunded_order"])

    def test_04_transient_error_does_not_emit_false_no_order(self):
        """TC-SHOPIFY-04: Outage raises retryable exception and never emits false no_order (R-11)."""
        self.shopify.simulated_outage = True
        with self.assertRaises(RuntimeError) as ctx:
            self.shopify.get_order_by_customer_email("any@example.com", reference_time=self.now)
        
        self.assertIn("Shopify 500", str(ctx.exception))
        self.assertEqual(self.shopify.retry_count, 1)

    def test_05_six_shopify_policies_synced_with_sha256_hash(self):
        """TC-SHOPIFY-05: Exactly 6 standard policies are fetched and hashed via proxy (R-17)."""
        policies = self.shopify.get_policies()
        self.assertEqual(set(policies.keys()), set(POLICY_TYPES))
        for ptype in POLICY_TYPES:
            self.assertIn("content", policies[ptype])
            self.assertIn("hash", policies[ptype])
            self.assertEqual(len(policies[ptype]["hash"]), 64, "SHA-256 hash must be 64 hex chars.")

    def test_06_live_product_search_resolves_actual_inventory_and_price(self):
        """TC-SHOPIFY-06: Live product search returns accurate facts without hallucinations (R-16)."""
        prod = self.shopify.search_product("Ceramic Table Lamp")
        self.assertIsNotNone(prod)
        self.assertEqual(prod["id"], "prod_2")
        self.assertEqual(prod["inventory"], 4)
        self.assertEqual(prod["price"], 89.00)


if __name__ == "__main__":
    unittest.main()
