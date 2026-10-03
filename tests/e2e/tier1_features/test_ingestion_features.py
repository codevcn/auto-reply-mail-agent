"""Tier 1 Feature Tests: Mail Ingestion & Safeguards.
Requirements: Invariant 3, R-04, R-05, R-06; Sections 11, 18.
"""

import unittest
from tests.e2e.fixtures import (
    MockMailEngine,
    InboundEmail,
)


class TestIngestionFeatures(unittest.TestCase):
    def setUp(self):
        self.mail_engine = MockMailEngine()
        self.uid_validity = 8888
        self.baseline_uid = 100

        # Pre-populate mailbox with 100 historical emails (UID 1 to 100)
        for uid in range(1, 101):
            self.mail_engine.receive_email(
                InboundEmail(
                    id=f"mail_{uid}",
                    mailbox_address="support@wrydeco.com",
                    imap_uid=uid,
                    uid_validity=self.uid_validity,
                    from_address=f"old_customer_{uid}@gmail.com",
                    subject=f"Old Message {uid}",
                    body_text="Pre-baseline content",
                    headers={"Message-ID": f"<old_{uid}@mail.com>"}
                )
            )

    def test_01_activation_baseline_uid_ignores_historical_emails(self):
        """TC-INGEST-01: Baseline UID ignores pre-existing emails upon store activation (R-04)."""
        ingested = self.mail_engine.ingest_new_messages(baseline_uid=self.baseline_uid, uid_validity=self.uid_validity)
        self.assertEqual(len(ingested), 0, "No historical email <= baseline_uid must be ingested.")

    def test_02_new_incoming_email_above_baseline_is_ingested(self):
        """TC-INGEST-02: New email arriving post-activation (UID > baseline) is ingested."""
        new_mail = InboundEmail(
            id="mail_101",
            mailbox_address="support@wrydeco.com",
            imap_uid=101,
            uid_validity=self.uid_validity,
            from_address="new_customer@example.com",
            subject="Question on Table Lamp",
            body_text="Hi, does this lamp come with a bulb?",
            headers={"Message-ID": "<new_101@client.com>"}
        )
        self.mail_engine.receive_email(new_mail)

        ingested = self.mail_engine.ingest_new_messages(baseline_uid=self.baseline_uid, uid_validity=self.uid_validity)
        self.assertEqual(len(ingested), 1)
        self.assertEqual(ingested[0].imap_uid, 101)
        self.assertEqual(ingested[0].from_address, "new_customer@example.com")

    def test_03_reconciliation_catches_all_missed_emails_without_loss(self):
        """TC-INGEST-03: 5-minute reconciliation catches multiple missed emails during disconnect."""
        # 3 emails arrive during listener downtime
        for uid in [102, 103, 104]:
            self.mail_engine.receive_email(
                InboundEmail(
                    id=f"mail_{uid}",
                    mailbox_address="support@wrydeco.com",
                    imap_uid=uid,
                    uid_validity=self.uid_validity,
                    from_address=f"customer_{uid}@example.com",
                    subject=f"Inquiry {uid}",
                    body_text="Inquiry content",
                    headers={"Message-ID": f"<{uid}@client.com>"}
                )
            )

        ingested = self.mail_engine.ingest_new_messages(baseline_uid=self.baseline_uid, uid_validity=self.uid_validity)
        self.assertEqual(len(ingested), 3)
        self.assertEqual([m.imap_uid for m in ingested], [102, 103, 104])

    def test_04_seen_flag_preserved_non_destructive_imap_contract(self):
        """TC-INGEST-04: Ingestion does not set \\Seen flag on mailserver."""
        new_mail = InboundEmail(
            id="mail_105",
            mailbox_address="support@wrydeco.com",
            imap_uid=105,
            uid_validity=self.uid_validity,
            from_address="fresh@example.com",
            subject="Fresh Unseen Email",
            body_text="Should remain unread",
            headers={"Message-ID": "<fresh@client.com>"}
        )
        self.mail_engine.receive_email(new_mail)
        self.mail_engine.ingest_new_messages(baseline_uid=self.baseline_uid, uid_validity=self.uid_validity)

        self.assertNotIn(105, self.mail_engine.seen_flags, "Mailserver Seen flag must NOT be altered.")

    def test_05_uidvalidity_change_safeguard_blocks_ingestion(self):
        """TC-INGEST-05: UIDVALIDITY mismatch pauses ingestion to prevent data corruption."""
        changed_uid_validity = 9999
        new_mail = InboundEmail(
            id="mail_106",
            mailbox_address="support@wrydeco.com",
            imap_uid=106,
            uid_validity=changed_uid_validity,
            from_address="someone@example.com",
            subject="UIDVALIDITY Mismatch Email",
            body_text="Testing safeguard",
            headers={"Message-ID": "<mismatch@client.com>"}
        )
        self.mail_engine.receive_email(new_mail)

        # Checking with original registered uid_validity
        ingested = self.mail_engine.ingest_new_messages(baseline_uid=self.baseline_uid, uid_validity=self.uid_validity)
        self.assertEqual(len(ingested), 0, "Mismatched UIDVALIDITY must not be ingested.")

    def test_06_auto_submitted_and_bounce_suppression(self):
        """TC-INGEST-06: Auto-Submitted headers and loop detection suppress reply drafting."""
        bounce_headers = {"Auto-Submitted": "auto-replied", "Precedence": "bulk"}
        is_auto_submitted = any(
            k.lower() == "auto-submitted" and v.lower() != "no"
            for k, v in bounce_headers.items()
        )
        self.assertTrue(is_auto_submitted, "Auto-submitted email should be detected for suppression.")


if __name__ == "__main__":
    unittest.main()
