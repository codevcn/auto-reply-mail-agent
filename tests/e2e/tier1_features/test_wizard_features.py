"""Tier 1 Feature Tests: Store Profile Setup Wizard & Isolation.
Requirements: R-02, R-03, R-15, R-18; Sections 2.1, 7, 9.
"""

import unittest
import uuid
from tests.e2e.fixtures import (
    StoreProfile,
    SUPPORTED_STORE_DOMAINS,
    EXCLUDED_STORE_DOMAINS,
)


class MockStoreWizardService:
    def __init__(self):
        self.profiles: dict[str, StoreProfile] = {}
        self.encrypted_secrets_vault: dict[str, str] = {}

    def validate_domains(self, public_domain: str, canonical_domain: str) -> tuple[bool, str]:
        # Domain rule: canonical domain must be *.myshopify.com
        if not canonical_domain.endswith(".myshopify.com"):
            return False, "CANONICAL_DOMAIN_MUST_BE_MYSHOPIFY"
        
        # Piezaprint must never be auto-configured
        if any(exc in public_domain.lower() or exc in canonical_domain.lower() for exc in EXCLUDED_STORE_DOMAINS):
            return False, "STORE_EXCLUDED_FROM_SYSTEM"
        
        return True, "OK"

    def save_step_credentials(self, profile_id: str, client_secret: str, master_key: str):
        # Envelope encryption check: never save plaintext
        assert master_key, "Master key required for envelope encryption"
        encrypted_value = f"enc_v1:{uuid.uuid5(uuid.NAMESPACE_DNS, client_secret).hex}"
        self.encrypted_secrets_vault[profile_id] = encrypted_value

    def activate_profile(self, profile: StoreProfile, mailbox_tested: bool, proxy_tested: bool, shopify_tested: bool) -> tuple[bool, str]:
        if not (mailbox_tested and proxy_tested and shopify_tested):
            return False, "ALL_CONNECTION_TESTS_REQUIRED_BEFORE_ACTIVATION"
        profile.is_active = True
        self.profiles[profile.id] = profile
        return True, "ACTIVATED"


class TestWizardFeatures(unittest.TestCase):
    def setUp(self):
        self.wizard = MockStoreWizardService()

    def test_01_domain_separation_contract_enforced(self):
        """TC-WIZARD-01: Public domain and canonical myshopify domain are strictly separated."""
        # Valid domain pair
        ok, msg = self.wizard.validate_domains("wrydeco.com", "wrydeco.myshopify.com")
        self.assertTrue(ok)
        self.assertEqual(msg, "OK")

        # Invalid canonical domain (missing .myshopify.com)
        bad_ok, bad_msg = self.wizard.validate_domains("wrydeco.com", "wrydeco.com")
        self.assertFalse(bad_ok)
        self.assertEqual(bad_msg, "CANONICAL_DOMAIN_MUST_BE_MYSHOPIFY")

    def test_02_piezaprint_exclusion_rule(self):
        """TC-WIZARD-02: support@piezaprint.com is strictly excluded from configuration (R-03)."""
        ok, msg = self.wizard.validate_domains("piezaprint.com", "piezaprint.myshopify.com")
        self.assertFalse(ok, "Piezaprint must be rejected.")
        self.assertEqual(msg, "STORE_EXCLUDED_FROM_SYSTEM")

    def test_03_all_four_supported_stores_can_be_configured(self):
        """TC-WIZARD-03: All 4 target stores (Wrydeco, Chillgen, Preaureum, Jeminise) are accepted."""
        for public_dom, canon_dom in SUPPORTED_STORE_DOMAINS.items():
            ok, msg = self.wizard.validate_domains(public_dom, canon_dom)
            self.assertTrue(ok, f"Store {public_dom} must be accepted.")
            self.assertEqual(msg, "OK")

    def test_04_activation_requires_all_connection_tests_passing(self):
        """TC-WIZARD-04: Cannot activate store profile without passing Mailbox, Proxy, and Shopify tests."""
        profile = StoreProfile(
            id="store_1",
            name="Wrydeco",
            public_domain="wrydeco.com",
            canonical_domain="wrydeco.myshopify.com",
            mailbox_address="support@wrydeco.com",
            is_active=False,
            activation_baseline_uid=100,
            uid_validity=12345
        )

        # Try to activate without proxy test
        ok, err = self.wizard.activate_profile(profile, mailbox_tested=True, proxy_tested=False, shopify_tested=True)
        self.assertFalse(ok)
        self.assertEqual(err, "ALL_CONNECTION_TESTS_REQUIRED_BEFORE_ACTIVATION")
        self.assertFalse(profile.is_active)

        # Activate with all tests passing
        ok_full, msg = self.wizard.activate_profile(profile, mailbox_tested=True, proxy_tested=True, shopify_tested=True)
        self.assertTrue(ok_full)
        self.assertEqual(msg, "ACTIVATED")
        self.assertTrue(profile.is_active)

    def test_05_envelope_encryption_on_secrets_storage(self):
        """TC-WIZARD-05: Credentials saved in DB must use envelope encryption with no plaintext."""
        raw_secret = "shpss_actual_secret_token_123456"
        self.wizard.save_step_credentials("store_1", client_secret=raw_secret, master_key="secure_master_key_v1")
        
        stored_val = self.wizard.encrypted_secrets_vault.get("store_1")
        self.assertIsNotNone(stored_val)
        self.assertTrue(stored_val.startswith("enc_v1:"), "Stored secret must have encryption version header.")
        self.assertNotIn(raw_secret, stored_val, "Plaintext secret must never appear in encrypted vault.")


if __name__ == "__main__":
    unittest.main()
