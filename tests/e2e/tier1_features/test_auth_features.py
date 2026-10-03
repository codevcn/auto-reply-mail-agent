"""Tier 1 Feature Tests: Authentication & Session Security.
Requirements: R-27, R-28, R-29, R-35; Sections 7, 20.
"""

import unittest
from tests.e2e.fixtures import MockAuthService, UserAccount


class TestAuthFeatures(unittest.TestCase):
    def setUp(self):
        self.auth = MockAuthService()
        self.admin = self.auth.bootstrap_admin("AdminUser", "valid_hash")

    def test_01_login_success_normalized_username_creates_session(self):
        """TC-AUTH-01: Username normalization and session generation."""
        # Case insensitive test: "ADMINUSER" should match "adminuser"
        success, session_id, error = self.auth.login("ADMINUSER", password_valid=True)
        self.assertTrue(success, "Login should succeed with normalized username.")
        self.assertIsNotNone(session_id, "Session ID must be created.")
        self.assertTrue(session_id.startswith("sess_"), "Session format must match opaque prefix.")
        self.assertIn(session_id, self.auth.sessions, "Session must exist in server-side session store.")

    def test_02_login_failure_with_wrong_password_records_audit(self):
        """TC-AUTH-02: Bad credentials reject login and emit audit log."""
        initial_audit_len = len(self.auth.audit_log)
        success, session_id, error = self.auth.login("adminuser", password_valid=False)
        self.assertFalse(success, "Login must fail with invalid password.")
        self.assertIsNone(session_id, "No session ID should be issued.")
        self.assertEqual(error, "INVALID_CREDENTIALS")
        
        # Verify audit log emitted
        self.assertGreater(len(self.auth.audit_log), initial_audit_len)
        last_log = self.auth.audit_log[-1]
        self.assertEqual(last_log["event"], "LOGIN_FAILED")
        self.assertEqual(last_log["username"], "adminuser")

    def test_03_logout_immediately_revokes_session(self):
        """TC-AUTH-03: Logout immediately revokes server-side session."""
        _, session_id, _ = self.auth.login("adminuser", password_valid=True)
        self.assertIn(session_id, self.auth.sessions)

        logged_out = self.auth.logout(session_id)
        self.assertTrue(logged_out, "Logout call must succeed.")
        self.assertNotIn(session_id, self.auth.sessions, "Session must be removed from DB immediately.")

    def test_04_self_disable_invariant_strictly_blocked(self):
        """TC-AUTH-04: User cannot disable own account (Invariant R-28)."""
        # Admin tries to disable Admin
        success, err = self.auth.disable_user(actor_user_id=self.admin.id, target_user_id=self.admin.id)
        self.assertFalse(success, "Self-disable must be strictly rejected.")
        self.assertEqual(err, "SELF_DISABLE_NOT_ALLOWED")
        self.assertTrue(self.admin.is_active, "Account must remain active.")

    def test_05_last_active_user_disable_invariant_blocked(self):
        """TC-AUTH-05: Cannot disable last active user in the system (Invariant R-28)."""
        # Create a second user so we can test acting on another user
        staff = self.auth.bootstrap_admin("staff_member", "valid_hash")
        
        # Now disable admin by staff
        ok, err = self.auth.disable_user(actor_user_id=staff.id, target_user_id=self.admin.id)
        self.assertTrue(ok, "Disabling admin when another user is active should succeed.")

        # Now only staff is active. Another attempt to disable staff must fail
        ok2, err2 = self.auth.disable_user(actor_user_id=self.admin.id, target_user_id=staff.id)
        self.assertFalse(ok2, "Disabling last active user must fail.")
        self.assertEqual(err2, "LAST_ACTIVE_USER_REQUIRED")
        self.assertTrue(staff.is_active, "Last active user must remain active.")

    def test_06_disable_user_immediately_revokes_all_active_sessions(self):
        """TC-AUTH-06: Disabling a user immediately invalidates all their active sessions."""
        staff = self.auth.bootstrap_admin("staff_two", "valid_hash")
        _, staff_session, _ = self.auth.login("staff_two", password_valid=True)
        self.assertIn(staff_session, self.auth.sessions)

        # Admin disables staff
        ok, _ = self.auth.disable_user(actor_user_id=self.admin.id, target_user_id=staff.id)
        self.assertTrue(ok)
        self.assertNotIn(staff_session, self.auth.sessions, "All staff sessions must be terminated immediately.")


if __name__ == "__main__":
    unittest.main()
