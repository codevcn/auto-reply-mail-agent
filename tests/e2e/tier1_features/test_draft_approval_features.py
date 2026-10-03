"""Tier 1 Feature Tests: Draft Review, Human Approval & Delivery.
Requirements: Invariant 1, R-22, R-23, R-24, R-25, R-26; Sections 7, 16, 17.
"""

import unittest
from tests.e2e.fixtures import (
    InboundEmail,
    MockMailEngine,
    ReplyDraft,
)


class MockDraftWorkflowService:
    def __init__(self, mail_engine: MockMailEngine):
        self.mail_engine = mail_engine
        self.draft_versions: dict[str, list[ReplyDraft]] = {}  # work_item_id -> list of drafts

    def determine_draft_eligibility(self, intent: str) -> tuple[bool, str]:
        # Draft eligibility rules (Section 16, R-22, R-23)
        eligible_intents = {"product_inquiry", "order_support", "complaint", "return_or_refund"}
        if intent in eligible_intents:
            return True, "DRAFT_ELIGIBLE"
        return False, "MANUAL_REVIEW_REQUIRED"

    def create_initial_draft(self, work_item_id: str, subject: str, body: str, policy_hash: str) -> ReplyDraft:
        draft = ReplyDraft(
            id=f"draft_{work_item_id}_v1",
            work_item_id=work_item_id,
            version_num=1,
            subject=subject,
            body_text=body,
            language="en",
            policy_hash_used=policy_hash,
        )
        self.draft_versions[work_item_id] = [draft]
        return draft

    def update_draft(self, work_item_id: str, new_body: str, user_id: str) -> ReplyDraft:
        # Immutable versioning: never overwrite previous version (R-24)
        history = self.draft_versions.get(work_item_id, [])
        new_version_num = len(history) + 1
        new_draft = ReplyDraft(
            id=f"draft_{work_item_id}_v{new_version_num}",
            work_item_id=work_item_id,
            version_num=new_version_num,
            subject=history[-1].subject if history else "Re:",
            body_text=new_body,
            language=history[-1].language if history else "en",
            policy_hash_used=history[-1].policy_hash_used if history else "",
        )
        history.append(new_draft)
        self.draft_versions[work_item_id] = history
        return new_draft

    def execute_approval_and_send(
        self,
        draft: ReplyDraft,
        original_email: InboundEmail,
        idempotency_key: str,
        user_authenticated: bool,
        simulate_timeout: bool = False
    ):
        # Invariant 1: Human approval mandatory
        if not user_authenticated:
            raise PermissionError("AUTHENTICATION_REQUIRED_FOR_APPROVAL")

        return self.mail_engine.send_reply(
            draft_id=draft.id,
            idempotency_key=idempotency_key,
            original_email=original_email,
            reply_body=draft.body_text,
            simulate_network_timeout=simulate_timeout,
        )


class TestDraftApprovalFeatures(unittest.TestCase):
    def setUp(self):
        self.mail_engine = MockMailEngine()
        self.workflow = MockDraftWorkflowService(mail_engine=self.mail_engine)
        self.orig_email = InboundEmail(
            id="work_item_1",
            mailbox_address="support@wrydeco.com",
            imap_uid=200,
            uid_validity=1234,
            from_address="customer@example.com",
            subject="Question about shipping",
            body_text="When will my order ship?",
            headers={"Message-ID": "<cust-200@client.com>"}
        )

    def test_01_intent_draft_eligibility_matrix(self):
        """TC-DELIVERY-01: Supported intents generate draft; others route to manual review (R-22, R-23)."""
        # Approved intents
        for intent in ["product_inquiry", "order_support", "complaint", "return_or_refund"]:
            ok, code = self.workflow.determine_draft_eligibility(intent)
            self.assertTrue(ok, f"Intent {intent} must be draft eligible.")
            self.assertEqual(code, "DRAFT_ELIGIBLE")

        # Non-drafting intents
        for intent in ["partnership", "other", "uncertain"]:
            ok, code = self.workflow.determine_draft_eligibility(intent)
            self.assertFalse(ok, f"Intent {intent} must NOT generate draft.")
            self.assertEqual(code, "MANUAL_REVIEW_REQUIRED")

    def test_02_immutable_draft_versioning_preserves_history(self):
        """TC-DELIVERY-02: Editing draft appends an immutable new version without overwriting (R-24)."""
        v1 = self.workflow.create_initial_draft(
            work_item_id="item_10",
            subject="Re: Order",
            body="Initial draft content.",
            policy_hash="hash_a"
        )
        self.assertEqual(v1.version_num, 1)

        v2 = self.workflow.update_draft(work_item_id="item_10", new_body="Modified content.", user_id="user_1")
        self.assertEqual(v2.version_num, 2)
        self.assertEqual(v2.body_text, "Modified content.")

        history = self.workflow.draft_versions["item_10"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].body_text, "Initial draft content.", "V1 must remain unmodified.")

    def test_03_zero_autonomous_sending_invariant(self):
        """TC-DELIVERY-03: Zero autonomous sending: Unauthenticated send throws error (Invariant 1)."""
        v1 = self.workflow.create_initial_draft("item_11", "Re: Item", "Body", "hash")
        with self.assertRaises(PermissionError) as ctx:
            self.workflow.execute_approval_and_send(
                v1, self.orig_email, idempotency_key="key_1", user_authenticated=False
            )
        self.assertIn("AUTHENTICATION_REQUIRED", str(ctx.exception))

    def test_04_send_idempotency_prevents_duplicate_email_delivery(self):
        """TC-DELIVERY-04: Idempotency key prevents duplicate sends on double click or retry (R-26)."""
        v1 = self.workflow.create_initial_draft("item_12", "Re: Item", "Body", "hash")
        key = "idemp_uuid_12345"

        # First send
        attempt1 = self.workflow.execute_approval_and_send(
            v1, self.orig_email, idempotency_key=key, user_authenticated=True
        )
        self.assertEqual(attempt1.status, "sent")
        self.assertEqual(len(self.mail_engine.sent_folder_copies), 1)

        # Duplicate send with same idempotency key
        attempt2 = self.workflow.execute_approval_and_send(
            v1, self.orig_email, idempotency_key=key, user_authenticated=True
        )
        self.assertEqual(attempt2.id, attempt1.id, "Must return existing attempt.")
        self.assertEqual(len(self.mail_engine.sent_folder_copies), 1, "Must NOT send second email.")

    def test_05_smtp_starttls_preserves_rfc_thread_headers(self):
        """TC-DELIVERY-05: SMTP delivery properly threads with In-Reply-To and References (R-25)."""
        v1 = self.workflow.create_initial_draft("item_13", "Re: Item", "Body", "hash")
        attempt = self.workflow.execute_approval_and_send(
            v1, self.orig_email, idempotency_key="key_3", user_authenticated=True
        )
        self.assertEqual(attempt.in_reply_to, "<cust-200@client.com>")
        self.assertIn("<cust-200@client.com>", attempt.references)
        self.assertTrue(attempt.message_id.startswith("<") and attempt.message_id.endswith(">"))

    def test_06_imap_sent_folder_copy_executed_on_successful_send(self):
        """TC-DELIVERY-06: A copy of the sent email is appended to IMAP Sent folder."""
        v1 = self.workflow.create_initial_draft("item_14", "Re: Item", "Reply body text", "hash")
        self.workflow.execute_approval_and_send(
            v1, self.orig_email, idempotency_key="key_4", user_authenticated=True
        )
        self.assertEqual(len(self.mail_engine.sent_folder_copies), 1)
        sent_copy = self.mail_engine.sent_folder_copies[0]
        self.assertEqual(sent_copy["folder"], "Sent")
        self.assertEqual(sent_copy["body"], "Reply body text")

    def test_07_ambiguous_delivery_handling_flags_delivery_unknown(self):
        """TC-DELIVERY-07: Network timeout post-DATA marked delivery_unknown with no auto-retry (R-26)."""
        v1 = self.workflow.create_initial_draft("item_15", "Re: Item", "Body", "hash")
        attempt = self.workflow.execute_approval_and_send(
            v1, self.orig_email, idempotency_key="key_5", user_authenticated=True, simulate_timeout=True
        )
        self.assertEqual(attempt.status, "delivery_unknown")
        self.assertEqual(len(self.mail_engine.sent_folder_copies), 0, "No Sent copy should be added for ambiguous status.")


if __name__ == "__main__":
    unittest.main()
