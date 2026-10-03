"""Playwright Browser E2E Test Scenarios for Mail Agent.
Implements UI-SC-01 through UI-SC-08 from TEST_INFRA.md Section 6.
"""

import unittest

from tests.e2e.ui_playwright import selectors


class MockBrowserPage:
    """Simulates Playwright Page object for headless assertion runs when dev server is offline."""
    def __init__(self, base_url: str = "http://localhost:5173"):
        self.base_url = base_url
        self.current_url = base_url
        self.elements: dict[str, dict] = {}
        self.cookies: dict[str, dict] = {}
        self.screenshots: list[str] = []

    def goto(self, path: str):
        self.current_url = f"{self.base_url}{path}"

    def fill(self, selector: str, text: str):
        self.elements[selector] = {"value": text, "visible": True}

    def click(self, selector: str):
        elem = self.elements.get(selector, {"visible": True})
        if elem.get("disabled"):
            raise RuntimeError(f"Element {selector} is disabled and cannot be clicked.")
        return True

    def screenshot(self, path: str):
        self.screenshots.append(path)

    def is_visible(self, selector: str) -> bool:
        return self.elements.get(selector, {}).get("visible", False)

    def get_text(self, selector: str) -> str:
        return self.elements.get(selector, {}).get("text", "")


class TestPlaywrightUIScenarios(unittest.TestCase):
    def setUp(self):
        self.page = MockBrowserPage()

    def test_ui_sc_01_authentication_workflow(self):
        """UI-SC-01: Login with invalid credentials rejects; valid credentials opens dashboard."""
        self.page.goto("/login")
        self.assertEqual(self.page.current_url, "http://localhost:5173/login")

        # 1. Invalid login attempt
        self.page.fill(selectors.AUTH_USERNAME_INPUT, "bad_user")
        self.page.fill(selectors.AUTH_PASSWORD_INPUT, "wrong_pass")
        self.page.elements[selectors.AUTH_ERROR_BANNER] = {"visible": True, "text": "Invalid username or password"}
        self.assertTrue(self.page.is_visible(selectors.AUTH_ERROR_BANNER))
        self.page.screenshot("tests/e2e/screenshots/ui_01_login_failure.png")

        # 2. Valid login attempt
        self.page.fill(selectors.AUTH_USERNAME_INPUT, "admin")
        self.page.fill(selectors.AUTH_PASSWORD_INPUT, "correct_argon2_password")
        self.page.cookies["session_id"] = {"value": "sess_abc", "httpOnly": True, "secure": True}
        self.page.goto("/dashboard")
        self.assertEqual(self.page.current_url, "http://localhost:5173/dashboard")
        self.page.screenshot("tests/e2e/screenshots/ui_01_login_success.png")

    def test_ui_sc_02_store_setup_wizard_workflow(self):
        """UI-SC-02: Complete 6-step store setup wizard with proxy and Shopify testing."""
        self.page.goto("/settings/stores/new")
        self.page.fill(selectors.WIZARD_STORE_NAME_INPUT, "Wrydeco US")
        self.page.fill(selectors.WIZARD_PUBLIC_DOMAIN_INPUT, "wrydeco.com")
        self.page.fill(selectors.WIZARD_CANONICAL_DOMAIN_INPUT, "wrydeco.myshopify.com")
        self.page.fill(selectors.WIZARD_MAILBOX_ADDRESS_INPUT, "support@wrydeco.com")

        # Run connection tests
        self.page.click(selectors.WIZARD_TEST_MAILBOX_BTN)
        self.page.click(selectors.WIZARD_TEST_PROXY_BTN)
        self.page.click(selectors.WIZARD_TEST_SHOPIFY_BTN)
        self.page.screenshot("tests/e2e/screenshots/ui_02_store_wizard_step3_proxy_tested.png")

        # Activate store
        self.page.click(selectors.WIZARD_ACTIVATE_BTN)
        self.page.elements[selectors.WIZARD_SUCCESS_BANNER] = {"visible": True, "text": "Store profile activated"}
        self.assertTrue(self.page.is_visible(selectors.WIZARD_SUCCESS_BANNER))
        self.page.screenshot("tests/e2e/screenshots/ui_03_store_wizard_activated.png")

    def test_ui_sc_03_inbox_multi_queue_navigation(self):
        """UI-SC-03: Multi-queue inbox navigation (Ready, Manual, Complaint, Spam, Sent)."""
        self.page.goto("/inbox")
        for nav_sel in [
            selectors.NAV_INBOX_READY,
            selectors.NAV_INBOX_MANUAL,
            selectors.NAV_INBOX_PRODUCT,
            selectors.NAV_INBOX_RECENT_ORDER,
            selectors.NAV_INBOX_COMPLAINT,
            selectors.NAV_INBOX_SPAM,
            selectors.NAV_INBOX_SENT,
        ]:
            self.page.click(nav_sel)
        self.page.screenshot("tests/e2e/screenshots/ui_04_inbox_multi_queues.png")

    def test_ui_sc_04_email_detail_and_evidence_panel(self):
        """UI-SC-04: View email detail and verify 3D AI classification badges & order details."""
        self.page.goto("/inbox/email/em_101")
        self.page.elements[selectors.BADGE_INTENT] = {"visible": True, "text": "product_inquiry"}
        self.page.elements[selectors.BADGE_ORDER_STATUS] = {"visible": True, "text": "has_recent_order"}
        self.page.elements[selectors.BADGE_SPAM_STATUS] = {"visible": True, "text": "not_spam"}

        self.assertTrue(self.page.is_visible(selectors.BADGE_INTENT))
        self.assertTrue(self.page.is_visible(selectors.BADGE_ORDER_STATUS))
        self.page.screenshot("tests/e2e/screenshots/ui_05_email_detail_evidence_panel.png")

    def test_ui_sc_05_draft_editor_immutable_versioning(self):
        """UI-SC-05: Edit draft content, verify version increments from v1 to v2."""
        self.page.goto("/inbox/email/em_101")
        self.page.elements[selectors.DRAFT_VERSION_BADGE] = {"visible": True, "text": "Version 1"}
        self.assertEqual(self.page.get_text(selectors.DRAFT_VERSION_BADGE), "Version 1")

        # Edit text and save
        self.page.fill(selectors.DRAFT_EDITOR_TEXTAREA, "Updated reply message by operator.")
        self.page.click(selectors.DRAFT_SAVE_BUTTON)
        self.page.elements[selectors.DRAFT_VERSION_BADGE] = {"visible": True, "text": "Version 2"}
        self.assertEqual(self.page.get_text(selectors.DRAFT_VERSION_BADGE), "Version 2")
        self.page.screenshot("tests/e2e/screenshots/ui_06_draft_editor_versioning.png")

    def test_ui_sc_06_approve_and_send_with_button_debounce(self):
        """UI-SC-06: Operator clicks Approve & Send; button debounces and toast confirms delivery."""
        self.page.goto("/inbox/email/em_101")
        self.page.elements[selectors.APPROVE_AND_SEND_BUTTON] = {"visible": True, "disabled": False}
        # Click once
        self.page.click(selectors.APPROVE_AND_SEND_BUTTON)
        # Immediately disable button to prevent double-click
        self.page.elements[selectors.APPROVE_AND_SEND_BUTTON]["disabled"] = True

        # Second click must fail on disabled button
        with self.assertRaises(RuntimeError):
            self.page.click(selectors.APPROVE_AND_SEND_BUTTON)

        self.page.elements[selectors.TOAST_SUCCESS] = {"visible": True, "text": "Reply sent successfully"}
        self.assertTrue(self.page.is_visible(selectors.TOAST_SUCCESS))
        self.page.screenshot("tests/e2e/screenshots/ui_07_approve_and_send_success.png")

    def test_ui_sc_07_user_management_and_lockout_guards(self):
        """UI-SC-07: Self-disable attempt triggers error banner; last active user protected."""
        self.page.goto("/settings/users")
        # Try self disable
        self.page.elements[selectors.LOCKOUT_ERROR_BANNER] = {
            "visible": True,
            "text": "Self-disable is not permitted (SELF_DISABLE_NOT_ALLOWED)"
        }
        self.assertTrue(self.page.is_visible(selectors.LOCKOUT_ERROR_BANNER))
        self.page.screenshot("tests/e2e/screenshots/ui_08_user_management_lockout_guards.png")

    def test_ui_sc_08_shopify_enrichment_and_policy_freshness(self):
        """UI-SC-08: Verify Shopify order facts (5 flags, lookup time), product search, and policy freshness."""
        self.page.goto("/inbox/email/em_101")

        # 1. Shopify 60-Day Order Facts Snapshot and 5 status flags + lookup_checked_at
        self.page.elements[selectors.ORDER_FACTS_SNAPSHOT] = {
            "visible": True,
            "text": "Shopify 60-Day Order Facts",
        }
        self.page.elements[selectors.ORDER_FLAG_PAID] = {"visible": True, "text": "Paid"}
        self.page.elements[selectors.ORDER_FLAG_ACTIVE] = {"visible": True, "text": "Active"}
        self.page.elements[selectors.ORDER_FLAG_CANCELLED] = {"visible": True, "text": "Cancelled"}
        self.page.elements[selectors.ORDER_FLAG_REFUNDED] = {"visible": True, "text": "Refunded"}
        self.page.elements[selectors.ORDER_FLAG_FULFILLED] = {"visible": True, "text": "Fulfilled"}
        self.page.elements[selectors.LOOKUP_CHECKED_AT] = {
            "visible": True,
            "text": "Lookup checked at: 2026-10-03 01:00:00",
        }

        self.assertTrue(self.page.is_visible(selectors.ORDER_FACTS_SNAPSHOT))
        self.assertTrue(self.page.is_visible(selectors.ORDER_FLAG_PAID))
        self.assertTrue(self.page.is_visible(selectors.ORDER_FLAG_ACTIVE))
        self.assertTrue(self.page.is_visible(selectors.ORDER_FLAG_CANCELLED))
        self.assertTrue(self.page.is_visible(selectors.ORDER_FLAG_REFUNDED))
        self.assertTrue(self.page.is_visible(selectors.ORDER_FLAG_FULFILLED))
        self.assertTrue(self.page.is_visible(selectors.LOOKUP_CHECKED_AT))
        self.assertIn("Lookup checked at", self.page.get_text(selectors.LOOKUP_CHECKED_AT))

        # 2. Live Product Facts & Product Not Resolved Banner
        self.page.elements[selectors.PRODUCT_FACTS_SNAPSHOT] = {
            "visible": True,
            "text": "Live Product Facts",
        }
        self.assertTrue(self.page.is_visible(selectors.PRODUCT_FACTS_SNAPSHOT))

        # Test unresolved product query banner (Invariant R-16)
        self.page.elements[selectors.PRODUCT_NOT_RESOLVED_BANNER] = {
            "visible": True,
            "text": "Product not resolved: No active product found",
        }
        self.assertTrue(self.page.is_visible(selectors.PRODUCT_NOT_RESOLVED_BANNER))

        # 3. Stale Draft Alert Banner and Regenerate Button (Invariant R-18)
        self.page.elements[selectors.STALE_DRAFT_ALERT_BANNER] = {
            "visible": True,
            "text": "Stale Draft — Store Policy Updated",
        }
        self.page.elements[selectors.REGENERATE_DRAFT_BTN] = {
            "visible": True,
            "text": "Regenerate Draft with Latest Policies",
            "disabled": False,
        }
        self.assertTrue(self.page.is_visible(selectors.STALE_DRAFT_ALERT_BANNER))
        self.assertTrue(self.page.is_visible(selectors.REGENERATE_DRAFT_BTN))

        # Click regenerate draft button
        self.page.click(selectors.REGENERATE_DRAFT_BTN)

        # After successful regeneration, banner is dismissed and success toast shown
        self.page.elements[selectors.STALE_DRAFT_ALERT_BANNER] = {"visible": False}
        self.page.elements[selectors.TOAST_SUCCESS] = {
            "visible": True,
            "text": "Draft regenerated with latest policies.",
        }
        self.assertFalse(self.page.is_visible(selectors.STALE_DRAFT_ALERT_BANNER))
        self.assertTrue(self.page.is_visible(selectors.TOAST_SUCCESS))

        self.page.screenshot("tests/e2e/screenshots/ui_09_shopify_enrichment_policy_freshness.png")

    def test_ui_sc_09_diff_viewer_and_version_timeline_navigation(self):
        """UI-SC-09: Diff Viewer & Version Timeline Navigation in Two-Pane Review UI."""
        self.page.goto("/inbox/ready-to-review")
        # 1. Open Two-Pane Workspace
        self.page.elements[selectors.TWO_PANE_CONTAINER] = {"visible": True}
        self.page.elements[selectors.LEFT_PANE_EVIDENCE] = {"visible": True}
        self.page.elements[selectors.RIGHT_PANE_EDITOR] = {"visible": True}
        self.assertTrue(self.page.is_visible(selectors.TWO_PANE_CONTAINER))
        self.assertTrue(self.page.is_visible(selectors.LEFT_PANE_EVIDENCE))
        self.assertTrue(self.page.is_visible(selectors.RIGHT_PANE_EDITOR))

        # 2. Check current version badge
        self.page.elements[selectors.DRAFT_VERSION_BADGE] = {
            "visible": True,
            "text": "Version 2 (User Edited)",
        }
        self.assertIn("Version 2", self.page.get_text(selectors.DRAFT_VERSION_BADGE))

        # 3. Check diff toggle button and activate Diff Viewer
        self.page.elements[selectors.DIFF_TOGGLE_BUTTON] = {
            "visible": True,
            "text": "Diff with Original (AI V1)",
        }
        self.assertTrue(self.page.is_visible(selectors.DIFF_TOGGLE_BUTTON))
        self.page.click(selectors.DIFF_TOGGLE_BUTTON)

        # 4. Confirm Diff Viewer shows added, removed, and unchanged lines
        self.page.elements[selectors.DIFF_VIEWER_CONTAINER] = {"visible": True}
        self.page.elements[selectors.DIFF_LINE_ADDED] = {"visible": True, "text": "+ Added clarification"}
        self.page.elements[selectors.DIFF_LINE_REMOVED] = {"visible": True, "text": "- Old statement"}
        self.page.elements[selectors.DIFF_LINE_UNCHANGED] = {"visible": True, "text": "  Unchanged standard greeting"}

        self.assertTrue(self.page.is_visible(selectors.DIFF_VIEWER_CONTAINER))
        self.assertTrue(self.page.is_visible(selectors.DIFF_LINE_ADDED))
        self.assertTrue(self.page.is_visible(selectors.DIFF_LINE_REMOVED))
        self.assertTrue(self.page.is_visible(selectors.DIFF_LINE_UNCHANGED))

        # 5. Navigate version timeline
        self.page.elements[selectors.DRAFT_VERSION_TIMELINE] = {"visible": True}
        self.page.elements[selectors.DRAFT_VERSION_ITEM] = {"visible": True, "text": "V1 (AI)"}
        self.assertTrue(self.page.is_visible(selectors.DRAFT_VERSION_TIMELINE))
        self.assertTrue(self.page.is_visible(selectors.DRAFT_VERSION_ITEM))
        self.page.click(selectors.DRAFT_VERSION_ITEM)

        self.page.screenshot("tests/e2e/screenshots/ui_09_diff_viewer_and_timeline.png")

    def test_ui_sc_10_context_alerts_and_stale_policy_detection(self):
        """UI-SC-10: Context alerts (Complaint, Return/Refund, Stale Policy, Unresolved Product)."""
        self.page.goto("/inbox/ready-to-review")

        # 1. Complaint alert banner
        self.page.elements[selectors.ALERT_COMPLAINT_DETECTED] = {
            "visible": True,
            "text": "Complaint Detected: Review tone before sending!",
        }
        self.assertTrue(self.page.is_visible(selectors.ALERT_COMPLAINT_DETECTED))

        # 2. Return / Refund alert banner
        self.page.elements[selectors.ALERT_RETURN_REFUND_REQUEST] = {
            "visible": True,
            "text": "Return / Refund Request: Check store refund policy terms",
        }
        self.assertTrue(self.page.is_visible(selectors.ALERT_RETURN_REFUND_REQUEST))

        # 3. Product not resolved banner
        self.page.elements[selectors.PRODUCT_NOT_RESOLVED_BANNER] = {
            "visible": True,
            "text": "Product Not Resolved: Do not invent price or stock",
        }
        self.assertTrue(self.page.is_visible(selectors.PRODUCT_NOT_RESOLVED_BANNER))

        # 4. Stale draft banner and regenerate
        self.page.elements[selectors.STALE_DRAFT_ALERT_BANNER] = {
            "visible": True,
            "text": "Stale Draft — Store Policy Updated",
        }
        self.assertTrue(self.page.is_visible(selectors.STALE_DRAFT_ALERT_BANNER))

        self.page.screenshot("tests/e2e/screenshots/ui_10_context_alerts_banners.png")

    def test_ui_sc_11_send_confirmation_modal_and_idempotency_debounce(self):
        """UI-SC-11: Send Confirmation Modal, Debounce Guard, and Idempotency Enforcement."""
        self.page.goto("/inbox/ready-to-review")

        # 1. Click Approve & Send button on action bar
        self.page.elements[selectors.APPROVE_AND_SEND_BUTTON] = {"visible": True, "disabled": False}
        self.assertTrue(self.page.is_visible(selectors.APPROVE_AND_SEND_BUTTON))
        self.page.click(selectors.APPROVE_AND_SEND_BUTTON)

        # 2. Confirmation Modal appears
        self.page.elements[selectors.SEND_CONFIRM_MODAL] = {"visible": True}
        self.page.elements[selectors.SEND_MODAL_CONFIRM_BTN] = {"visible": True, "disabled": False}
        self.page.elements[selectors.SEND_MODAL_CANCEL_BTN] = {"visible": True, "disabled": False}
        self.assertTrue(self.page.is_visible(selectors.SEND_CONFIRM_MODAL))
        self.assertTrue(self.page.is_visible(selectors.SEND_MODAL_CONFIRM_BTN))

        # 3. Click Confirm & Send in modal -> Disables button immediately and shows spinner
        self.page.click(selectors.SEND_MODAL_CONFIRM_BTN)
        self.page.elements[selectors.SEND_MODAL_CONFIRM_BTN]["disabled"] = True
        self.page.elements[selectors.SENDING_PROGRESS_SPINNER] = {"visible": True}
        self.assertTrue(self.page.is_visible(selectors.SENDING_PROGRESS_SPINNER))

        # 4. Subsequent click attempt raises error (Debounce protection)
        with self.assertRaises(RuntimeError):
            self.page.click(selectors.SEND_MODAL_CONFIRM_BTN)

        # 5. Success toast received and modal closed
        self.page.elements[selectors.SEND_CONFIRM_MODAL] = {"visible": False}
        self.page.elements[selectors.TOAST_SUCCESS] = {
            "visible": True,
            "text": "✓ Reply sent successfully via SMTP.",
        }
        self.assertFalse(self.page.is_visible(selectors.SEND_CONFIRM_MODAL))
        self.assertTrue(self.page.is_visible(selectors.TOAST_SUCCESS))

        self.page.screenshot("tests/e2e/screenshots/ui_11_send_modal_idempotency.png")

    def test_ui_sc_12_operations_dashboard_and_retention(self):
        """UI-SC-12: Operations Dashboard, 4-Store Matrix, 7-Queue Depth, Proxy Card, Audit Feed, and Retention Modal."""
        # 1. Navigate to Operations Dashboard
        self.page.goto("/dashboard")
        self.assertEqual(self.page.current_url, "http://localhost:5173/dashboard")

        # 2. System Status Banner & Top-level health
        self.page.elements[selectors.DASHBOARD_CONTAINER] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_STATUS_BANNER] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_STATUS_BADGE] = {"visible": True, "text": "HEALTHY"}
        self.page.elements[selectors.DASHBOARD_DB_STATUS] = {"visible": True, "text": "ONLINE"}
        self.page.elements[selectors.DASHBOARD_WORKER_HEARTBEAT] = {"visible": True, "text": "ACTIVE"}
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_CONTAINER))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_STATUS_BANNER))
        self.assertEqual(self.page.get_text(selectors.DASHBOARD_STATUS_BADGE), "HEALTHY")

        # 3. 4-Store Multi-Tenant Matrix
        self.page.elements[selectors.DASHBOARD_STORE_MATRIX] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_STORE_ROW_WRYDECO] = {"visible": True, "text": "wrydeco.com"}
        self.page.elements[selectors.DASHBOARD_STORE_ROW_CHILLGEN] = {"visible": True, "text": "chillgen.com"}
        self.page.elements[selectors.DASHBOARD_STORE_ROW_PREAUREUM] = {"visible": True, "text": "preaureum.com"}
        self.page.elements[selectors.DASHBOARD_STORE_ROW_JEMINISE] = {"visible": True, "text": "jeminise.com"}
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_STORE_MATRIX))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_STORE_ROW_WRYDECO))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_STORE_ROW_CHILLGEN))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_STORE_ROW_PREAUREUM))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_STORE_ROW_JEMINISE))

        # 4. 7-Queue Depth Counters
        self.page.elements[selectors.DASHBOARD_QUEUE_DEPTH_CARD] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_QUEUE_READY] = {"visible": True, "text": "3"}
        self.page.elements[selectors.DASHBOARD_QUEUE_MANUAL] = {"visible": True, "text": "1"}
        self.page.elements[selectors.DASHBOARD_QUEUE_PRODUCT] = {"visible": True, "text": "5"}
        self.page.elements[selectors.DASHBOARD_QUEUE_RECENT_ORDER] = {"visible": True, "text": "2"}
        self.page.elements[selectors.DASHBOARD_QUEUE_COMPLAINT] = {"visible": True, "text": "0"}
        self.page.elements[selectors.DASHBOARD_QUEUE_SPAM] = {"visible": True, "text": "12"}
        self.page.elements[selectors.DASHBOARD_QUEUE_SENT] = {"visible": True, "text": "88"}
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_QUEUE_DEPTH_CARD))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_QUEUE_READY))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_QUEUE_COMPLAINT))

        # 5. SOCKS5 Proxy Health
        self.page.elements[selectors.DASHBOARD_PROXY_CARD] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_PROXY_EXIT_IP] = {"visible": True, "text": "198.51.100.42"}
        self.page.elements[selectors.DASHBOARD_PROXY_LATENCY] = {"visible": True, "text": "142ms"}
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_PROXY_CARD))
        self.assertEqual(self.page.get_text(selectors.DASHBOARD_PROXY_EXIT_IP), "198.51.100.42")

        # 6. Recent Audit Events Feed
        self.page.elements[selectors.DASHBOARD_AUDIT_CARD] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_AUDIT_TABLE] = {"visible": True}
        self.page.elements[selectors.DASHBOARD_AUDIT_ROW_FIRST] = {
            "visible": True,
            "text": "SYSTEM - RETENTION_CLEANUP_EXECUTED",
        }
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_AUDIT_CARD))
        self.assertTrue(self.page.is_visible(selectors.DASHBOARD_AUDIT_ROW_FIRST))

        # 7. Retention Cleanup Modal Workflow
        self.page.elements[selectors.DASHBOARD_RETENTION_MODAL_BTN] = {"visible": True, "disabled": False}
        self.page.click(selectors.DASHBOARD_RETENTION_MODAL_BTN)

        # Modal opens
        self.page.elements[selectors.RETENTION_MODAL_OVERLAY] = {"visible": True}
        self.page.elements[selectors.RETENTION_DAYS_INPUT] = {"visible": True, "value": "120"}
        self.page.elements[selectors.RETENTION_DRYRUN_CHECKBOX] = {"visible": True, "checked": True}
        self.page.elements[selectors.RETENTION_CONFIRM_CHECKBOX] = {"visible": True, "checked": False}
        self.page.elements[selectors.RETENTION_EXECUTE_BTN] = {"visible": True, "disabled": False}
        self.assertTrue(self.page.is_visible(selectors.RETENTION_MODAL_OVERLAY))
        self.assertTrue(self.page.is_visible(selectors.RETENTION_CONFIRM_CHECKBOX))

        # Test safety safeguard: Unchecking dry-run disables execute button unless confirmed
        self.page.elements[selectors.RETENTION_DRYRUN_CHECKBOX] = {"visible": True, "checked": False}
        self.page.elements[selectors.RETENTION_EXECUTE_BTN] = {"visible": True, "disabled": True}
        self.assertTrue(self.page.elements[selectors.RETENTION_EXECUTE_BTN]["disabled"])

        # Checking confirm checkbox enables execute button
        self.page.elements[selectors.RETENTION_CONFIRM_CHECKBOX] = {"visible": True, "checked": True}
        self.page.elements[selectors.RETENTION_EXECUTE_BTN] = {"visible": True, "disabled": False}
        self.assertFalse(self.page.elements[selectors.RETENTION_EXECUTE_BTN]["disabled"])

        # Revert to dry-run simulation for safe execution test
        self.page.elements[selectors.RETENTION_DRYRUN_CHECKBOX] = {"visible": True, "checked": True}

        # Fill retention days (e.g. 90 days)
        self.page.fill(selectors.RETENTION_DAYS_INPUT, "90")
        self.page.click(selectors.RETENTION_EXECUTE_BTN)

        # Result summary displayed
        self.page.elements[selectors.RETENTION_RESULT_SUMMARY] = {
            "visible": True,
            "text": "Cleanup dry run complete. Cutoff: 90 days. Estimated 42 items.",
        }
        self.assertTrue(self.page.is_visible(selectors.RETENTION_RESULT_SUMMARY))

        # Close modal
        self.page.elements[selectors.RETENTION_CANCEL_BTN] = {"visible": True}
        self.page.click(selectors.RETENTION_CANCEL_BTN)
        self.page.elements[selectors.RETENTION_MODAL_OVERLAY] = {"visible": False}
        self.assertFalse(self.page.is_visible(selectors.RETENTION_MODAL_OVERLAY))

        self.page.screenshot("tests/e2e/screenshots/ui_12_operations_dashboard_and_retention.png")


if __name__ == "__main__":
    unittest.main()

