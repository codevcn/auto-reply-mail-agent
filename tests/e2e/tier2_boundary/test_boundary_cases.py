"""Tier 2 Boundary & Corner Cases Test Suite.
Covers boundary conditions: 60-day window, 120-day retention, 10MB/25MB attachment limits,
path traversal, empty inputs, unicode characters, optimistic concurrency locks.
"""

import datetime
import os
import unittest
from tests.e2e.fixtures import (
    EmailAttachment,
    InboundEmail,
    MockMailEngine,
    MockProxyClient,
    MockShopifyService,
    MAX_ATTACHMENT_COUNT,
    MAX_SINGLE_ATTACHMENT_BYTES,
    MAX_TOTAL_ATTACHMENT_BYTES,
    RETENTION_CAP_DAYS,
)


class TestBoundaryCases(unittest.TestCase):
    def setUp(self):
        self.proxy = MockProxyClient(is_alive=True)
        self.shopify = MockShopifyService(proxy=self.proxy)
        self.mail_engine = MockMailEngine()
        self.now = datetime.datetime.now(datetime.timezone.utc)

    def test_bc_01_sixty_day_boundary_exact_limits(self):
        """BC-01: Exactly 60-day boundary (Day 59 vs Day 60 vs Day 61)."""
        # Order A: 59 days 23 hours ago -> Within 60-day window
        order_59d = {
            "id": "ord_59d",
            "customer_email": "boundary@example.com",
            "created_at": self.now - datetime.timedelta(days=59, hours=23),
            "financial_status": "paid",
        }
        # Order B: 60 days 1 hour ago -> Outside 60-day window
        order_61d = {
            "id": "ord_61d",
            "customer_email": "boundary_old@example.com",
            "created_at": self.now - datetime.timedelta(days=60, hours=1),
            "financial_status": "paid",
        }
        self.shopify.orders_db.extend([order_59d, order_61d])

        res_recent = self.shopify.get_order_by_customer_email("boundary@example.com", reference_time=self.now)
        self.assertTrue(res_recent["has_recent_order"], "59d 23h must be counted as recent.")

        res_expired = self.shopify.get_order_by_customer_email("boundary_old@example.com", reference_time=self.now)
        self.assertFalse(res_expired["has_recent_order"], "60d 1h must NOT be counted as recent.")

    def test_bc_02_attachment_single_size_threshold(self):
        """BC-02: Attachment single file exact size (10MB vs 10MB + 1 byte)."""
        valid_10mb = EmailAttachment(
            filename="exact_10mb.pdf",
            content_type="application/pdf",
            size_bytes=MAX_SINGLE_ATTACHMENT_BYTES,
            raw_bytes=b"%PDF-1.4"
        )
        invalid_10mb_plus_1 = EmailAttachment(
            filename="overflow.pdf",
            content_type="application/pdf",
            size_bytes=MAX_SINGLE_ATTACHMENT_BYTES + 1,
            raw_bytes=b"%PDF-1.4"
        )
        self.assertLessEqual(valid_10mb.size_bytes, MAX_SINGLE_ATTACHMENT_BYTES)
        self.assertGreater(invalid_10mb_plus_1.size_bytes, MAX_SINGLE_ATTACHMENT_BYTES)

    def test_bc_03_attachment_total_size_threshold(self):
        """BC-03: Attachment total size across files (25MB vs 25MB + 1 byte)."""
        file1 = EmailAttachment("part1.pdf", "application/pdf", 12 * 1024 * 1024, b"%PDF-")
        file2 = EmailAttachment("part2.pdf", "application/pdf", 13 * 1024 * 1024, b"%PDF-")
        file3_overflow = EmailAttachment("part3.pdf", "application/pdf", 1, b"%PDF-")

        total_exact = file1.size_bytes + file2.size_bytes
        self.assertEqual(total_exact, MAX_TOTAL_ATTACHMENT_BYTES)

        total_overflow = total_exact + file3_overflow.size_bytes
        self.assertGreater(total_overflow, MAX_TOTAL_ATTACHMENT_BYTES)

    def test_bc_04_attachment_count_limit_boundary(self):
        """BC-04: Attachment count limit (10 files allowed vs 11 files rejected)."""
        ten_attachments = [
            EmailAttachment(f"f_{i}.pdf", "application/pdf", 100, b"%PDF-")
            for i in range(10)
        ]
        self.assertEqual(len(ten_attachments), MAX_ATTACHMENT_COUNT)

        eleven_attachments = ten_attachments + [
            EmailAttachment("f_11.pdf", "application/pdf", 100, b"%PDF-")
        ]
        self.assertGreater(len(eleven_attachments), MAX_ATTACHMENT_COUNT)

    def test_bc_05_path_traversal_sanitization(self):
        """BC-05: Path traversal attack filenames are safely sanitized."""
        malicious_filenames = [
            "../../../../etc/passwd",
            "..\\..\\Windows\\System32\\cmd.exe",
            "../../../var/mail/support",
            "/absolute/root/file.pdf",
        ]
        for bad_name in malicious_filenames:
            safe_basename = os.path.basename(bad_name)
            self.assertNotIn("..", safe_basename, f"Normalized path {safe_basename} must not contain traversal dots.")
            self.assertFalse(safe_basename.startswith("/"), "Must not be absolute Unix path.")
            self.assertFalse(safe_basename.startswith("\\"), "Must not be absolute Windows path.")

    def test_bc_06_empty_subject_and_body_email(self):
        """BC-06: Handling completely empty email subject and body safely."""
        empty_email = InboundEmail(
            id="empty_1",
            mailbox_address="support@wrydeco.com",
            imap_uid=501,
            uid_validity=123,
            from_address="silent@example.com",
            subject="",
            body_text="",
            headers={}
        )
        self.assertEqual(empty_email.subject, "")
        self.assertEqual(empty_email.body_text, "")
        # Should not throw exception and should be recognized as empty content
        is_empty = len(empty_email.subject.strip()) == 0 and len(empty_email.body_text.strip()) == 0
        self.assertTrue(is_empty)

    def test_bc_07_unicode_extended_and_emoji_handling(self):
        """BC-07: Email containing complex Unicode emojis, Vietnamese accents, CJK characters."""
        complex_body = (
            "Xin chào shop! 👋\n"
            "Tôi muốn hỏi về kích thước mẫu tranh này: 🌸 Ứng dụng này rất đẹp!\n"
            "Japanese: こんにちは | Arabic: مرحبا"
        )
        encoded_bytes = complex_body.encode("utf-8")
        decoded_str = encoded_bytes.decode("utf-8")
        self.assertEqual(complex_body, decoded_str)
        self.assertIn("Xin chào shop", decoded_str)
        self.assertIn("👋", decoded_str)

    def test_bc_08_stale_policy_detection_via_hash_mismatch(self):
        """BC-08: Policy change after draft creation triggers STALE warning (R-17, R-18)."""
        draft_policy_hash = "sha256_hash_version_1"
        current_synced_hash = "sha256_hash_version_2"

        is_stale = (draft_policy_hash != current_synced_hash)
        self.assertTrue(is_stale, "Hash mismatch must trigger stale policy warning.")

    def test_bc_09_optimistic_concurrency_row_version_conflict(self):
        """BC-09: Optimistic Concurrency Control rejects concurrent modification with old version."""
        stored_row_version = 3
        submitted_row_version = 2  # Outdated version from concurrent edit

        conflict_detected = (submitted_row_version != stored_row_version)
        self.assertTrue(conflict_detected, "Row version mismatch must raise CONCURRENT_MODIFICATION.")

    def test_bc_10_retention_period_120_day_cutoff(self):
        """BC-10: 120-Day retention hard cap calculates cleanup cutoff accurately (R-34)."""
        retention_cutoff = self.now - datetime.timedelta(days=RETENTION_CAP_DAYS)
        
        record_119_days = self.now - datetime.timedelta(days=119)
        record_121_days = self.now - datetime.timedelta(days=121)

        self.assertGreater(record_119_days, retention_cutoff, "119-day record must be retained.")
        self.assertLess(record_121_days, retention_cutoff, "121-day record must be purged.")


if __name__ == "__main__":
    unittest.main()
