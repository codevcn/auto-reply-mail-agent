"""Phase 7 Challenger Empirical Adversarial Test Harness.

Adversarial Stress Testing covering:
1. Invariant R-33 (Host Topology, Port Isolation & Non-Disruption):
   - Quét compose.yaml: Xác minh chỉ có 127.0.0.1:8090:8090 trên host.
   - Xác minh PostgreSQL 5432 hoàn toàn không có port mapping ra host.
   - Xác minh worker không có port mapping ra host.
   - Xác minh không có port 25, 465, 587, 993, 8089, 8088 ra host.
   - Xác minh bridge network mail-agent-net được định nghĩa và gán cho các services.
2. Nginx Hardening & SSE Buffering Bypass:
   - Quét deploy/nginx/mail-agent.conf:
     * Server name: mail-agent.wrydeco.com
     * Upstream: proxy_pass http://127.0.0.1:8090
     * SSE route: proxy_buffering off, proxy_cache off, chunked_transfer_encoding on, proxy_read_timeout 86400s
     * Security headers: HSTS, X-Frame-Options DENY, X-Content-Type-Options nosniff, CSP, Referrer-Policy
     * Health probes: bypass cache/logs
3. Outbound Mail Kill-Switch & Anti-Replay Guard:
   - Quét deploy/scripts/restore.sh:
     * Worker & API shutdown trước khi restore SQL
     * In-flight job neutralization: SQL update email_jobs PENDING/IN_PROGRESS/RETRY -> CANCELLED
     * Reset drafts mid-send: SENDING -> NEEDS_MANUAL_REVIEW
     * API starts first, liveness/readiness probes verified before worker starts
4. Invariant R-34 & Resource Quotas & Logging:
   - Quét compose.yaml: CPU/RAM quotas (API 1.0/1G, Worker 1.0/1G, DB 0.5/512M)
   - Log rotation json-file 50mx5 cho tất cả containers
   - Quét deploy/scripts/backup.sh: RETENTION_DAYS clamped/capped at 120, pruning > RETENTION_DAYS
5. Invariant R-34 (Retention Service & Pydantic Validation Stress):
   - Config validation stress: RETENTION_DAYS = 121, 200, 0 ném ValidationError
   - Empirical in-memory batch cleanup với PRAGMA foreign_keys=ON:
     * Bẻ xích foreign key (current_version_id = NULL) trước khi xóa version
     * Xóa sạch draft versions, delivery attempts, snapshots, attachments, jobs, incoming emails, audit events quá 120 ngày
     * Bảo toàn 100% core tenant entities (StoreProfile, User, Mailbox, MailboxCheckpoint, StorePolicy, ProxyProfile)
   - Dry-Run Accuracy: Đếm chính xác số lượng nhưng không xóa bất kỳ dòng nào
   - Zero-PII Logging Audit: Kiểm tra log phát ra chỉ có aggregate count, tuyệt đối không có PII
6. CLI & API Stress:
   - CLI retention-cleanup với --days 121 / 200 / 0 ném lỗi exit code 1
7. System Health Probes & SLA Warning:
   - SLA oldest unreviewed draft cảnh báo khi > 12h (43200s) và critical khi > 24h (86400s)
8. Frontend Operations Dashboard & ADS Design System Token Conformance:
   - Kiểm tra OperationsDashboardPage.tsx tuân thủ ADS semantic tokens (--ds-*)
"""

from __future__ import annotations

# ruff: noqa: E402
import datetime
import logging
import os
import re
import sys
import unittest
import uuid

# Ensure project root and backend are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
backend_path = os.path.join(PROJECT_ROOT, "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.cli import retention_cleanup_async
from app.config import Settings
from app.db.base import Base
from app.db.models.audit import AuditEvent
from app.db.models.draft import ReplyDeliveryAttempt, ReplyDraft, ReplyDraftVersion
from app.db.models.email import (
    EmailAttachmentMetadata,
    EmailClassification,
    EmailJob,
    IncomingEmail,
    MailboxCheckpoint,
)
from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
from app.db.models.store import Mailbox, ProxyProfile, StorePolicy, StoreProfile
from app.db.models.user import User
from app.retention.service import RetentionService
from app.system.service import SystemHealthService
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class TestPhase7EmpiricalChallenger(unittest.IsolatedAsyncioTestCase):
    """Empirical adversarial verification suite executed by Challenger Phase 7."""

    def setUp(self):
        self.compose_path = os.path.join(PROJECT_ROOT, "compose.yaml")
        self.nginx_path = os.path.join(PROJECT_ROOT, "deploy", "nginx", "mail-agent.conf")
        self.backup_path = os.path.join(PROJECT_ROOT, "deploy", "scripts", "backup.sh")
        self.restore_path = os.path.join(PROJECT_ROOT, "deploy", "scripts", "restore.sh")
        self.runbook_path = os.path.join(PROJECT_ROOT, "deploy", "RUNBOOK.md")
        self.dashboard_fe_path = os.path.join(
            PROJECT_ROOT, "frontend", "src", "pages", "OperationsDashboardPage.tsx"
        )

    # --------------------------------------------------------------------------
    # 1. Invariant R-33: VPS Hardening & Port Topology Verification
    # --------------------------------------------------------------------------
    def test_invariant_r33_compose_port_isolation(self):
        """Verify compose.yaml strictly isolates PostgreSQL and publishes ONLY 127.0.0.1:8090."""
        self.assertTrue(os.path.isfile(self.compose_path), "compose.yaml must exist")
        with open(self.compose_path, encoding="utf-8") as f:
            compose_content = f.read()

        # Find all port mapping lines
        port_lines = [
            line.strip()
            for line in compose_content.splitlines()
            if line.strip().startswith("- \"") and ":" in line
        ]

        # 1. Verify exactly one published port mapping exists across the entire compose file
        self.assertEqual(
            len(port_lines),
            1,
            f"Expected exactly 1 host port mapping in compose.yaml, found: {port_lines}",
        )
        self.assertIn(
            "127.0.0.1:8090:8090",
            port_lines[0],
            f"Host port mapping must be strictly 127.0.0.1:8090:8090, found: {port_lines[0]}",
        )

        # 2. Verify PostgreSQL has NO published ports
        db_section = re.search(r"mail-agent-db:.*?(?=networks:|$)", compose_content, re.DOTALL)
        self.assertIsNotNone(db_section, "mail-agent-db section must exist")
        db_text = db_section.group(0)
        self.assertNotIn("ports:", db_text, "mail-agent-db must NOT have a ports mapping")
        self.assertNotIn("5432:5432", db_text, "Port 5432 must never be mapped to host")

        # 3. Verify mail-agent-worker has NO published ports
        worker_section = re.search(
            r"mail-agent-worker:.*?(?=mail-agent-db:|$)", compose_content, re.DOTALL
        )
        self.assertIsNotNone(worker_section, "mail-agent-worker section must exist")
        worker_text = worker_section.group(0)
        self.assertNotIn("ports:", worker_text, "mail-agent-worker must NOT have a ports mapping")

        # 4. Invariant R-33: Verify NO collision with existing VPS mail ports or webmail ports
        forbidden_ports = ["25", "465", "587", "993", "8089", "8088", "5432"]
        for p in forbidden_ports:
            for pl in port_lines:
                self.assertNotIn(
                    f":{p}",
                    pl,
                    f"Forbidden port {p} found in published ports: {pl} (violates Invariant R-33)",
                )

        # 5. Verify isolated network definition
        self.assertIn("mail-agent-net:", compose_content)
        self.assertIn("driver: bridge", compose_content)

    # --------------------------------------------------------------------------
    # 2. Nginx Reverse Proxy & SSE Buffering Bypass
    # --------------------------------------------------------------------------
    def test_nginx_reverse_proxy_and_sse_hardening(self):
        """Verify deploy/nginx/mail-agent.conf routes to 127.0.0.1:8090 and disables SSE buffering."""
        self.assertTrue(os.path.isfile(self.nginx_path), "deploy/nginx/mail-agent.conf must exist")
        with open(self.nginx_path, encoding="utf-8") as f:
            nginx_content = f.read()

        # Domain and proxy targets
        self.assertIn("server_name mail-agent.wrydeco.com;", nginx_content)
        self.assertIn("proxy_pass http://127.0.0.1:8090;", nginx_content)

        # SSE location block with buffering disabled
        self.assertIn("location ~* ^/api/(generation|events|logs)/stream", nginx_content)
        self.assertIn("proxy_buffering off;", nginx_content)
        self.assertIn("proxy_cache off;", nginx_content)
        self.assertIn("chunked_transfer_encoding on;", nginx_content)
        self.assertIn("proxy_read_timeout 86400s;", nginx_content)

        # Security Headers
        self.assertIn("Strict-Transport-Security", nginx_content)
        self.assertIn('add_header X-Frame-Options "DENY"', nginx_content)
        self.assertIn('add_header X-Content-Type-Options "nosniff"', nginx_content)
        self.assertIn("Content-Security-Policy", nginx_content)
        self.assertIn('add_header Referrer-Policy "strict-origin-when-cross-origin"', nginx_content)
        self.assertIn('add_header X-XSS-Protection "1; mode=block"', nginx_content)

        # Health probes
        self.assertIn("location ~* ^/health/(live|ready)", nginx_content)

    # --------------------------------------------------------------------------
    # 3. Outbound Mail Kill-Switch & Anti-Replay Guard in restore.sh
    # --------------------------------------------------------------------------
    def test_restore_script_outbound_kill_switch_and_job_neutralization(self):
        """Verify restore.sh stops worker before restore and neutralizes pending jobs."""
        self.assertTrue(os.path.isfile(self.restore_path), "deploy/scripts/restore.sh must exist")
        with open(self.restore_path, encoding="utf-8") as f:
            restore_content = f.read()

        # STEP 2: Kill-Switch must precede database restoration
        step2_idx = restore_content.find("[STEP 2/7] Activating Outbound Mail Kill-Switch")
        step3_idx = restore_content.find("[STEP 3/7] Replaying database schema")
        step4_idx = restore_content.find("[STEP 4/7] Neutralizing pending outbound jobs")
        step5_idx = restore_content.find("[STEP 5/7] Starting mail-agent-api")
        step7_idx = restore_content.find("[STEP 7/7] Safely restarting mail-agent-worker")

        self.assertTrue(
            step2_idx > 0 and step3_idx > step2_idx and step4_idx > step3_idx,
            "Restore execution order must be: Kill-Switch -> Replay DB -> Neutralize Jobs",
        )
        self.assertTrue(
            step5_idx > step4_idx and step7_idx > step5_idx,
            "API must start and verify health before worker starts",
        )

        # Kill-switch command: stop worker and API
        self.assertIn(
            "docker compose stop mail-agent-worker mail-agent-api",
            restore_content,
            "Must stop worker and API to halt outbound SMTP dispatch",
        )

        # Neutralize jobs in SQL: PENDING/IN_PROGRESS/RETRY -> CANCELLED
        self.assertIn("UPDATE email_jobs", restore_content)
        self.assertIn("SET status = 'CANCELLED'", restore_content)
        self.assertIn("WHERE status IN ('PENDING', 'IN_PROGRESS', 'RETRY')", restore_content)

        # Reset drafts in SENDING -> NEEDS_MANUAL_REVIEW
        self.assertIn("UPDATE reply_drafts", restore_content)
        self.assertIn("SET status = 'NEEDS_MANUAL_REVIEW'", restore_content)
        self.assertIn("WHERE status = 'SENDING'", restore_content)

    # --------------------------------------------------------------------------
    # 4. Resource Quotas & Log Rotation in compose.yaml & backup.sh
    # --------------------------------------------------------------------------
    def test_resource_quotas_and_log_rotation(self):
        """Verify CPU/RAM limits and json-file 50mx5 log rotation in compose.yaml."""
        with open(self.compose_path, encoding="utf-8") as f:
            compose_content = f.read()

        # Quotas for API (1.0 CPU, 1024M), Worker (1.0 CPU, 1024M), DB (0.5 CPU, 512M)
        self.assertIn('cpus: "1.0"', compose_content)
        self.assertIn("memory: 1024M", compose_content)
        self.assertIn('cpus: "0.5"', compose_content)
        self.assertIn("memory: 512M", compose_content)

        # Log rotation for all containers
        log_driver_count = compose_content.count('driver: "json-file"')
        self.assertGreaterEqual(log_driver_count, 3, "All 3 services must use json-file logging")
        max_size_count = compose_content.count('max-size: "50m"')
        self.assertGreaterEqual(max_size_count, 3, "All 3 services must enforce max-size 50m")
        max_file_count = compose_content.count('max-file: "5"')
        self.assertGreaterEqual(max_file_count, 3, "All 3 services must enforce max-file 5")

        # backup.sh retention check
        with open(self.backup_path, encoding="utf-8") as f:
            backup_content = f.read()
        self.assertIn("RETENTION_DAYS", backup_content)
        self.assertIn("120", backup_content)
        self.assertIn("-delete", backup_content)
        self.assertIn("docker exec", backup_content)

    # --------------------------------------------------------------------------
    # 5. Invariant R-34: Pydantic Validation Stress Testing
    # --------------------------------------------------------------------------
    def test_retention_days_pydantic_validation_stress(self):
        """Adversarial Stress Test: Verify Settings strictly rejects RETENTION_DAYS > 120 or < 1."""
        # 1. Valid settings (120, 60, 1)
        s120 = Settings(
            SESSION_SECRET="a" * 64,
            ENCRYPTION_MASTER_KEY="b" * 64,
            GCP_PROJECT_ID="test-proj",
            RETENTION_DAYS=120,
        )
        self.assertEqual(s120.RETENTION_DAYS, 120)

        s60 = Settings(
            SESSION_SECRET="a" * 64,
            ENCRYPTION_MASTER_KEY="b" * 64,
            GCP_PROJECT_ID="test-proj",
            RETENTION_DAYS=60,
        )
        self.assertEqual(s60.RETENTION_DAYS, 60)

        s1 = Settings(
            SESSION_SECRET="a" * 64,
            ENCRYPTION_MASTER_KEY="b" * 64,
            GCP_PROJECT_ID="test-proj",
            RETENTION_DAYS=1,
        )
        self.assertEqual(s1.RETENTION_DAYS, 1)

        # 2. Adversarial attacks: RETENTION_DAYS = 121 (must fail)
        with self.assertRaises(ValidationError) as ctx:
            Settings(
                SESSION_SECRET="a" * 64,
                ENCRYPTION_MASTER_KEY="b" * 64,
                GCP_PROJECT_ID="test-proj",
                RETENTION_DAYS=121,
            )
        self.assertIn("RETENTION_DAYS", str(ctx.exception))

        # 3. Adversarial attacks: RETENTION_DAYS = 200 (must fail)
        with self.assertRaises(ValidationError) as ctx:
            Settings(
                SESSION_SECRET="a" * 64,
                ENCRYPTION_MASTER_KEY="b" * 64,
                GCP_PROJECT_ID="test-proj",
                RETENTION_DAYS=200,
            )
        self.assertIn("RETENTION_DAYS", str(ctx.exception))

        # 4. Adversarial attacks: RETENTION_DAYS = 0 (must fail)
        with self.assertRaises(ValidationError):
            Settings(
                SESSION_SECRET="a" * 64,
                ENCRYPTION_MASTER_KEY="b" * 64,
                GCP_PROJECT_ID="test-proj",
                RETENTION_DAYS=0,
            )

        # 5. Adversarial attacks: RETENTION_DAYS = -10 (must fail)
        with self.assertRaises(ValidationError):
            Settings(
                SESSION_SECRET="a" * 64,
                ENCRYPTION_MASTER_KEY="b" * 64,
                GCP_PROJECT_ID="test-proj",
                RETENTION_DAYS=-10,
            )

    # --------------------------------------------------------------------------
    # 6. Foreign Key Integrity, Batching & Complete Preservation Stress Testing
    # --------------------------------------------------------------------------
    async def test_retention_service_fk_integrity_and_preservation_stress(self):
        """Empirically test RetentionService deletes expired workflow data with PRAGMA foreign_keys=ON.

        Ensures:
        - 0 Foreign Key constraint violations when breaking circular draft references.
        - 100% of expired workflow entities deleted.
        - 100% preservation of core tenant data: StoreProfile, User, Mailbox, MailboxCheckpoint, StorePolicy, ProxyProfile.
        - System audit event recorded.
        """
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        # Turn ON foreign key enforcement strictly to catch any FK cascade/breakage failure
        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with session_factory() as session:
            # 1. Seed Core Tenant Data (Must NEVER be deleted per Invariant R-34)
            user = User(
                id=uuid.uuid4(),
                username="operator_phase7",
                normalized_username="operator_phase7",
                password_hash="argon2id$mocked",
                status="active",
            )
            store = StoreProfile(
                id=uuid.uuid4(),
                name="Wrydeco Production",
                brand_name="Wrydeco",
                public_domain="wrydeco.com",
                status="active",
            )
            proxy = ProxyProfile(
                id=uuid.uuid4(),
                name="US Datacenter Proxy",
                host="127.0.0.1",
                port=1080,
                enabled=True,
            )
            session.add_all([user, store, proxy])
            await session.flush()

            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="support@wrydeco.com",
                encrypted_password="mocked_password",
                imap_host="103.147.123.63",
                smtp_host="103.147.123.63",
                status="active",
            )
            checkpoint = MailboxCheckpoint(
                id=uuid.uuid4(),
                mailbox_id=mailbox.id,
                folder="INBOX",
                uid_validity=1,
                activation_baseline_uid=1000,
                last_durably_enqueued_uid=1000,
                state="idle",
            )
            policy = StorePolicy(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                policy_type="refund_policy",
                title="Refund Policy",
                body_text="30 days refund",
                content_hash="mocked_hash_123",
            )
            session.add_all([mailbox, checkpoint, policy])
            await session.flush()

            # 2. Seed Expired Workflow Data (> 120 days ago: 150 days old)
            expired_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=150)
            email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=99991,
                uidvalidity=1,
                sender_email="customer@example.com",
                recipient_email="support@wrydeco.com",
                subject="Old Inquiry",
                received_at=expired_time,
                created_at=expired_time,
                status="pending_approval",
            )
            session.add(email)
            await session.flush()

            draft = ReplyDraft(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                incoming_email_id=email.id,
                status="draft",
                created_at=expired_time,
            )
            session.add(draft)
            await session.flush()

            version = ReplyDraftVersion(
                id=uuid.uuid4(),
                draft_id=draft.id,
                incoming_email_id=email.id,
                version_number=1,
                subject="Re: Old Inquiry",
                body_text="Thank you for contacting us.",
                body_html="<p>Thank you for contacting us.</p>",
                content_hash="mock_hash_abc",
                created_at=expired_time,
            )
            session.add(version)
            await session.flush()

            # Circular self-reference: reply_drafts.current_version_id -> reply_draft_versions.id
            draft.current_version_id = version.id

            delivery = ReplyDeliveryAttempt(
                id=uuid.uuid4(),
                incoming_email_id=email.id,
                draft_id=draft.id,
                draft_version_id=version.id,
                idempotency_key=str(uuid.uuid4()),
                approved_by=user.id,
                approved_at=expired_time,
                status="sent",
                created_at=expired_time,
            )

            order_snap = ShopifyOrderSnapshot(
                id=uuid.uuid4(),
                incoming_email_id=email.id,
                store_profile_id=store.id,
                customer_email="customer@example.com",
                lookup_status="no_order",
                lookup_checked_at=expired_time,
                created_at=expired_time,
            )
            product_snap = ShopifyProductSnapshot(
                id=uuid.uuid4(),
                incoming_email_id=email.id,
                store_profile_id=store.id,
                search_query="tshirt",
                raw_query_terms=["tshirt"],
                matched_products=[],
                warning_codes=[],
                fetched_at=expired_time,
            )
            session.add_all([delivery, order_snap, product_snap])
            await session.flush()

            email.order_snapshot_id = order_snap.id
            email.product_snapshot_id = product_snap.id

            classification = EmailClassification(
                id=uuid.uuid4(),
                incoming_email_id=email.id,
                intent="product_inquiry",
                created_at=expired_time,
            )
            attachment = EmailAttachmentMetadata(
                id=uuid.uuid4(),
                incoming_email_id=email.id,
                filename="receipt.pdf",
                content_type="application/pdf",
                size_bytes=2048,
                sha256_hash="hash_pdf_123",
                created_at=expired_time,
            )
            job = EmailJob(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                incoming_email_id=email.id,
                job_type="classify",
                status="completed",
                created_at=expired_time,
            )
            job2 = EmailJob(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                incoming_email_id=email.id,
                job_type="generate_draft",
                status="completed",
                created_at=expired_time,
            )
            audit = AuditEvent(
                id=uuid.uuid4(),
                event_type="EMAIL_INGESTED",
                actor_user_id=user.id,
                created_at=expired_time,
            )
            session.add_all([classification, attachment, job, job2, audit])
            await session.commit()

            # 3. Seed Fresh Workflow Data (< 120 days ago: 10 days old)
            fresh_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=10)
            fresh_email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=99992,
                uidvalidity=1,
                sender_email="fresh_customer@example.com",
                recipient_email="support@wrydeco.com",
                subject="Fresh Inquiry",
                received_at=fresh_time,
                created_at=fresh_time,
                status="pending_approval",
            )
            session.add(fresh_email)
            await session.commit()

            # Verify initial counts
            emails_before = (await session.execute(select(IncomingEmail))).scalars().all()
            self.assertEqual(len(emails_before), 2)

            # 4. Execute Retention Cleanup Job (Live deletion mode)
            report = await RetentionService.run_cleanup_job(
                db=session,
                retention_days=120,
                dry_run=False,
                batch_size=500,
                actor_user_id=user.id,
            )

            self.assertEqual(report.status, "completed")
            self.assertFalse(report.dry_run)
            self.assertEqual(report.deleted_counts.incoming_emails, 1)
            self.assertEqual(report.deleted_counts.reply_drafts, 1)
            self.assertEqual(report.deleted_counts.reply_draft_versions, 1)
            self.assertEqual(report.deleted_counts.reply_delivery_attempts, 1)
            self.assertEqual(report.deleted_counts.shopify_order_snapshots, 1)
            self.assertEqual(report.deleted_counts.shopify_product_snapshots, 1)
            self.assertEqual(report.deleted_counts.email_classifications, 1)
            self.assertEqual(report.deleted_counts.email_attachment_metadata, 1)
            self.assertGreaterEqual(report.deleted_counts.email_jobs, 2)
            self.assertEqual(report.deleted_counts.audit_events, 1)

            # 5. Assert Expired Workflow Data Deleted
            expired_email_chk = (
                await session.execute(select(IncomingEmail).where(IncomingEmail.id == email.id))
            ).scalar_one_or_none()
            self.assertIsNone(expired_email_chk, "Expired IncomingEmail must be deleted")

            draft_chk = (
                await session.execute(select(ReplyDraft).where(ReplyDraft.id == draft.id))
            ).scalar_one_or_none()
            self.assertIsNone(draft_chk, "Expired ReplyDraft must be deleted")

            # 6. Assert Fresh Workflow Data PRESERVED
            fresh_email_chk = (
                await session.execute(select(IncomingEmail).where(IncomingEmail.id == fresh_email.id))
            ).scalar_one_or_none()
            self.assertIsNotNone(fresh_email_chk, "Fresh IncomingEmail must be preserved")

            # 7. Assert Core Tenant Entities 100% PRESERVED
            stores = (await session.execute(select(StoreProfile))).scalars().all()
            self.assertEqual(len(stores), 1, "StoreProfile must NEVER be deleted by retention")

            users = (await session.execute(select(User))).scalars().all()
            self.assertEqual(len(users), 1, "User must NEVER be deleted by retention")

            mailboxes = (await session.execute(select(Mailbox))).scalars().all()
            self.assertEqual(len(mailboxes), 1, "Mailbox must NEVER be deleted by retention")

            checkpoints = (await session.execute(select(MailboxCheckpoint))).scalars().all()
            self.assertEqual(len(checkpoints), 1, "MailboxCheckpoint must NEVER be deleted by retention")

            policies = (await session.execute(select(StorePolicy))).scalars().all()
            self.assertEqual(len(policies), 1, "StorePolicy must NEVER be deleted by retention")

            proxies = (await session.execute(select(ProxyProfile))).scalars().all()
            self.assertEqual(len(proxies), 1, "ProxyProfile must NEVER be deleted by retention")

            # 8. Assert Retention Audit Event Recorded
            audit_events = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "RETENTION_CLEANUP_EXECUTED")
                )
            ).scalars().all()
            self.assertEqual(len(audit_events), 1, "RETENTION_CLEANUP_EXECUTED audit event must be created")

        await engine.dispose()

    # --------------------------------------------------------------------------
    # 7. Dry-Run Accuracy Stress Testing
    # --------------------------------------------------------------------------
    async def test_retention_dry_run_accuracy(self):
        """Verify dry_run=True accurately counts records without deleting any database row."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with session_factory() as session:
            # Seed store and mailbox
            store = StoreProfile(
                id=uuid.uuid4(),
                name="DryRun Store",
                brand_name="DryRun",
                public_domain="dryrun.com",
                status="active",
            )
            session.add(store)
            await session.flush()

            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="dryrun@wrydeco.com",
                encrypted_password="mock",
                status="active",
            )
            session.add(mailbox)
            await session.flush()

            expired_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=130)
            old_email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=888,
                uidvalidity=1,
                sender_email="dryrun_cust@test.com",
                recipient_email="dryrun@wrydeco.com",
                received_at=expired_time,
                created_at=expired_time,
                status="pending_approval",
            )
            session.add(old_email)
            await session.commit()

            # Execute dry-run cleanup
            report = await RetentionService.run_cleanup_job(
                db=session,
                retention_days=120,
                dry_run=True,
            )

            self.assertTrue(report.dry_run)
            self.assertEqual(report.deleted_counts.incoming_emails, 1)

            # Empirically verify row count in DB is untouched
            emails_remaining = (
                await session.execute(select(IncomingEmail).where(IncomingEmail.id == old_email.id))
            ).scalar_one_or_none()
            self.assertIsNotNone(emails_remaining, "Dry-run must NOT delete any record from database")

        await engine.dispose()

    # --------------------------------------------------------------------------
    # 8. Zero-PII Logging Audit
    # --------------------------------------------------------------------------
    async def test_retention_zero_pii_logging_audit(self):
        """Audits log records emitted during cleanup to verify 100% absence of customer PII."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        captured_records: list[logging.LogRecord] = []

        class ListLogHandler(logging.Handler):
            def emit(self, record):
                captured_records.append(record)

        retention_logger = logging.getLogger("mail_agent.retention")
        handler = ListLogHandler()
        retention_logger.addHandler(handler)
        retention_logger.setLevel(logging.INFO)

        try:
            async with session_factory() as session:
                store = StoreProfile(
                    id=uuid.uuid4(),
                    name="PII Store",
                    brand_name="PIIStore",
                    public_domain="piistore.com",
                    status="active",
                )
                session.add(store)
                await session.flush()

                mailbox = Mailbox(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    address="pii@wrydeco.com",
                    encrypted_password="mock",
                    status="active",
                )
                session.add(mailbox)
                await session.flush()

                pii_customer_email = "victim_secret_client_99@superprivate.org"
                pii_subject = "CONFIDENTIAL MEDICAL RECORD 7788"

                expired_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=140)
                email = IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    imap_uid=777,
                    uidvalidity=1,
                    sender_email=pii_customer_email,
                    recipient_email="pii@wrydeco.com",
                    subject=pii_subject,
                    received_at=expired_time,
                    created_at=expired_time,
                    status="pending_approval",
                )
                session.add(email)
                await session.commit()

                # Run live cleanup
                await RetentionService.run_cleanup_job(
                    db=session,
                    retention_days=120,
                    dry_run=False,
                )

                # Inspect captured logs
                self.assertGreater(len(captured_records), 0, "Retention log must be emitted")

                for rec in captured_records:
                    msg = rec.getMessage()
                    extra_data = getattr(rec, "__dict__", {})

                    # Must NOT contain PII email or subject
                    self.assertNotIn(
                        pii_customer_email,
                        msg,
                        f"Customer email found in retention log message: {msg}",
                    )
                    self.assertNotIn(
                        pii_subject,
                        msg,
                        f"Customer email subject found in retention log message: {msg}",
                    )

                    # Must NOT contain PII in extra fields
                    for k, v in extra_data.items():
                        self.assertNotIn(
                            pii_customer_email,
                            str(v),
                            f"Customer email found in log extra dict [{k}]: {v}",
                        )
                        self.assertNotIn(
                            pii_subject,
                            str(v),
                            f"Customer email subject found in log extra dict [{k}]: {v}",
                        )

        finally:
            retention_logger.removeHandler(handler)
            await engine.dispose()

    # --------------------------------------------------------------------------
    # 9. CLI Retention Cleanup Stress Testing
    # --------------------------------------------------------------------------
    async def test_cli_retention_cleanup_stress(self):
        """Stress test retention-cleanup CLI enforcing Invariant R-34."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with session_factory() as session:
            # Over 120 days: must exit with code 1
            code_121 = await retention_cleanup_async(days=121, dry_run=True, session=session)
            self.assertEqual(code_121, 1)

            code_200 = await retention_cleanup_async(days=200, dry_run=True, session=session)
            self.assertEqual(code_200, 1)

            code_zero = await retention_cleanup_async(days=0, dry_run=True, session=session)
            self.assertEqual(code_zero, 1)

            code_neg = await retention_cleanup_async(days=-5, dry_run=True, session=session)
            self.assertEqual(code_neg, 1)

            # Valid 120 days: must exit with code 0
            code_valid = await retention_cleanup_async(days=120, dry_run=True, session=session)
            self.assertEqual(code_valid, 0)

        await engine.dispose()

    # --------------------------------------------------------------------------
    # 10. System Health Probes & SLA Warning
    # --------------------------------------------------------------------------
    async def test_system_health_sla_calculation(self):
        """Verify SystemHealthService reports SLA warning if oldest unreviewed draft > 12h."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with session_factory() as session:
            store = StoreProfile(
                id=uuid.uuid4(),
                name="Wrydeco Health",
                brand_name="Wrydeco",
                public_domain="wrydeco.com",
                status="active",
            )
            session.add(store)
            await session.flush()

            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="support@wrydeco.com",
                encrypted_password="mock",
                status="active",
            )
            session.add(mailbox)
            await session.flush()

            # Seed an email received 14 hours ago in pending_approval
            now_utc = datetime.datetime.now(datetime.UTC)
            old_time = now_utc - datetime.timedelta(hours=14)
            email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=123,
                uidvalidity=1,
                sender_email="sla@test.com",
                recipient_email="support@wrydeco.com",
                received_at=old_time,
                created_at=old_time,
                status="pending_approval",
                classification_category="product_inquiry",
                spam_status="not_spam",
            )
            session.add(email)
            await session.commit()

            summary = await SystemHealthService.get_health_summary(db=session)

            self.assertIsNotNone(summary.queues.oldest_unreviewed_age_seconds)
            self.assertGreater(
                summary.queues.oldest_unreviewed_age_seconds,
                43200.0,
                "Oldest unreviewed draft age must exceed 12h (43200s) SLA threshold",
            )
            self.assertEqual(summary.queues.ready_to_review, 1)

            # Seed an email received 25 hours ago (> 24h critical backlog threshold)
            critical_time = now_utc - datetime.timedelta(hours=25)
            email_crit = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=124,
                uidvalidity=1,
                sender_email="crit@test.com",
                recipient_email="support@wrydeco.com",
                received_at=critical_time,
                created_at=critical_time,
                status="pending_approval",
                classification_category="product_inquiry",
                spam_status="not_spam",
            )
            session.add(email_crit)
            await session.commit()

            summary_crit = await SystemHealthService.get_health_summary(db=session)
            self.assertTrue(
                summary_crit.queues.is_backlog_critical,
                "Oldest unreviewed email > 24h must trigger is_backlog_critical = True",
            )

        await engine.dispose()

    # --------------------------------------------------------------------------
    # 11. Frontend ADS Semantic Design Tokens Conformance
    # --------------------------------------------------------------------------
    def test_frontend_operations_dashboard_ads_tokens(self):
        """Verify OperationsDashboardPage.tsx strictly employs ADS semantic tokens and test-ids."""
        self.assertTrue(
            os.path.isfile(self.dashboard_fe_path),
            "frontend/src/pages/OperationsDashboardPage.tsx must exist",
        )
        with open(self.dashboard_fe_path, encoding="utf-8") as f:
            fe_content = f.read()

        # Check for key ADS CSS semantic variables
        self.assertIn("var(--ds-background-", fe_content)
        self.assertIn("var(--ds-text-", fe_content)
        self.assertIn("var(--ds-border-", fe_content)

        # Check for key test IDs (supporting both selectors.py contract and challenger aliases)
        self.assertTrue(
            'data-testid="operations-dashboard-container"' in fe_content
            or 'data-testid="dashboard-container"' in fe_content,
            "Must have dashboard container testid",
        )
        self.assertTrue(
            'data-testid="system-status-banner"' in fe_content
            or 'data-testid="dashboard-status-banner"' in fe_content,
            "Must have status banner testid",
        )
        self.assertTrue(
            'data-testid="store-health-matrix"' in fe_content
            or 'data-testid="store-matrix-card"' in fe_content,
            "Must have store matrix testid",
        )
        self.assertIn('data-testid="queue-depth-card"', fe_content)
        self.assertTrue(
            'data-testid="proxy-connectivity-card"' in fe_content
            or 'data-testid="proxy-health-card"' in fe_content,
            "Must have proxy card testid",
        )
        self.assertTrue(
            'data-testid="retention-modal"' in fe_content
            or 'data-testid="retention-modal-overlay"' in fe_content,
            "Must have retention modal testid",
        )


if __name__ == "__main__":
    unittest.main()
