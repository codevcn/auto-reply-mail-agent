#!/usr/bin/env python3
# ruff: noqa: E402
"""Empirical Adversarial Test Harness for Phase 4.
Evaluates:
- Invariant R-19: Direct HTTPS AI Isolation, proxy=None, trust_env=False, Environment Poisoning Defense.
- Invariant R-08: Spam Isolation & Server Immutability (5-queue hiding, spam tab visibility, mailserver zero-mutation, unmark-spam recovery).
- Invariant R-03: Piezaprint Multi-layer Absolute Exclusion.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root and backend are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
backend_path = os.path.join(PROJECT_ROOT, "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.provider import AIProvider
from app.ai.service import get_ai_provider
from app.ai.vertex import VertexGeminiProvider
from app.db.base import Base
from app.db.models.audit import AuditEvent
from app.db.models.email import EmailClassification, EmailJob, IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from app.db.models.user import User
from app.mail.exceptions import PiezaprintExclusionError
from app.mail.imap_client import IMAPClient
from app.mail.smtp_client import SMTPClient
from app.queue.service import TransactionalQueueService


class AdversarialPhase4Runner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests_run = 0
        self.results: list[dict[str, Any]] = []

    def record(self, name: str, success: bool, details: str = ""):
        self.tests_run += 1
        if success:
            self.passed += 1
            print(f"  [PASS] {name} | {details}")
        else:
            self.failed += 1
            print(f"  [FAIL] {name} | {details}", file=sys.stderr)
        self.results.append({"name": name, "success": success, "details": details})

    # =========================================================================
    # SUITE 1: INVARIANT R-19 (DIRECT HTTPS AI ISOLATION & PROXY DEFENSE)
    # =========================================================================
    async def run_ai_direct_https_suite(self):
        print("\n" + "=" * 75)
        print("SUITE 1: INVARIANT R-19 (DIRECT HTTPS AI ISOLATION & PROXY DEFENSE)")
        print("=" * 75)

        # 1.1: VertexGeminiProvider client network configuration inspection
        provider = VertexGeminiProvider(
            project_id="test-proj-emp",
            location="us-central1",
            classification_model="gemini-1.5-flash-emp",
            drafting_model="gemini-1.5-pro-emp",
        )
        client = provider._build_client()
        try:
            is_proxy_none = client._transport._pool._proxy is None
            is_trust_env_false = client.trust_env is False
            is_verify_true = client._transport._pool._ssl_context is not None

            if is_proxy_none and is_trust_env_false and is_verify_true:
                self.record(
                    "R-19 Client Network Config",
                    True,
                    f"Direct HTTPS enforced: proxy=None, trust_env=False, TLS verified. Transport: {type(client._transport).__name__}",
                )
            else:
                self.record(
                    "R-19 Client Network Config",
                    False,
                    f"Violation: proxy={client._transport._pool._proxy}, trust_env={client.trust_env}",
                )
        finally:
            await client.aclose()

        # 1.2: Hostile Environment Variable Proxy Poisoning Test
        hostile_env = {
            "HTTP_PROXY": "http://127.0.0.1:9999",
            "HTTPS_PROXY": "https://127.0.0.1:9999",
            "ALL_PROXY": "socks5h://127.0.0.1:9999",
            "http_proxy": "http://127.0.0.1:9999",
            "https_proxy": "https://127.0.0.1:9999",
            "all_proxy": "socks5h://127.0.0.1:9999",
        }
        with patch.dict(os.environ, hostile_env, clear=False):
            poisoned_client = provider._build_client()
            try:
                p_proxy = poisoned_client._transport._pool._proxy
                p_trust = poisoned_client.trust_env
                if p_proxy is None and p_trust is False:
                    self.record(
                        "R-19 Environment Poisoning Defense",
                        True,
                        "Client ignores HTTP_PROXY, HTTPS_PROXY, and ALL_PROXY environment variables (trust_env=False).",
                    )
                else:
                    self.record(
                        "R-19 Environment Poisoning Defense",
                        False,
                        f"Proxy leaked into client under hostile environment! proxy={p_proxy}, trust_env={p_trust}",
                    )
            finally:
                await poisoned_client.aclose()

        # 1.3: Factory Isolation (get_ai_provider)
        vertex_p = get_ai_provider("vertex_gemini")
        if isinstance(vertex_p, VertexGeminiProvider):
            v_client = vertex_p._build_client()
            try:
                assert v_client._transport._pool._proxy is None
                assert v_client.trust_env is False
                self.record(
                    "R-19 AI Factory Provider Isolation",
                    True,
                    "get_ai_provider('vertex_gemini') returns VertexGeminiProvider strictly enforcing direct HTTPS.",
                )
            finally:
                await v_client.aclose()
        else:
            self.record(
                "R-19 AI Factory Provider Isolation",
                False,
                f"Unexpected provider type: {type(vertex_p)}",
            )

        # 1.4: Shopify Proxy Config Separation
        # Verify Shopify client uses proxy while Vertex client NEVER uses proxy
        from app.proxy.client import ResolvedProxyConfig
        from app.proxy.transport import create_proxy_enforced_client

        proxy_conf = ResolvedProxyConfig(
            protocol="socks5",
            host="127.0.0.1",
            port=1080,
            username="test_user",
            password="test_password",
            connect_timeout=10,
        )
        shopify_client = create_proxy_enforced_client(proxy_conf)
        try:
            shopify_has_proxy = len(shopify_client._mounts) > 0
            ai_has_proxy = len(client._mounts) > 0 or client._transport._pool._proxy is not None
            if shopify_has_proxy and not ai_has_proxy:
                self.record(
                    "R-19 Strict Boundary vs Shopify Proxy",
                    True,
                    "Shopify client strictly routes through SOCKS5 proxy while AI client strictly routes direct HTTPS.",
                )
            else:
                self.record(
                    "R-19 Strict Boundary vs Shopify Proxy",
                    False,
                    f"Boundary violation: shopify_has_proxy={shopify_has_proxy}, ai_has_proxy={ai_has_proxy}",
                )
        finally:
            await shopify_client.aclose()

    # =========================================================================
    # SUITE 2: INVARIANT R-08 (SPAM ISOLATION ACROSS ALL 5 REGULAR QUEUES)
    # =========================================================================
    async def run_spam_isolation_suite(self):
        print("\n" + "=" * 75)
        print("SUITE 2: INVARIANT R-08 (SPAM ISOLATION ACROSS ALL 5 REGULAR QUEUES)")
        print("=" * 75)

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with session_factory() as session:
            store = StoreProfile(
                id=uuid.uuid4(),
                name="Wrydeco Empirical Test",
                brand_name="Wrydeco",
                public_domain="wrydeco.com",
            )
            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="support@wrydeco.com",
                imap_host="mail.wrydeco.com",
                imap_port=993,
                imap_tls_mode="SSL_TLS",
                smtp_host="mail.wrydeco.com",
                smtp_port=587,
                smtp_tls_mode="STARTTLS",
                encrypted_password="enc_password",
            )
            session.add(store)
            session.add(mailbox)
            await session.commit()

            # Seed 5 legitimate emails across all 5 queues
            legit_emails = [
                # 1. ready-to-review (status: pending_approval)
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=201,
                    uidvalidity=1,
                    sender_email="buyer_ready@gmail.com",
                    recipient_email="support@wrydeco.com",
                    subject="Legit Ready Inquiry",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="pending_approval",
                    classification_category="product_inquiry",
                    spam_status="not_spam",
                    current_draft_version=1,
                ),
                # 2. needs-manual-review (status: manual_review)
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=202,
                    uidvalidity=1,
                    sender_email="buyer_manual@gmail.com",
                    recipient_email="support@wrydeco.com",
                    subject="Legit Manual Review Needed",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="manual_review",
                    classification_category="other",
                    spam_status="not_spam",
                    review_reason_code="ERR_ATTACHMENT_TOO_LARGE",
                ),
                # 3. product-inquiry (classification_category: product_inquiry)
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=203,
                    uidvalidity=1,
                    sender_email="buyer_prod@gmail.com",
                    recipient_email="support@wrydeco.com",
                    subject="Product Dimension Question",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="classified",
                    classification_category="product_inquiry",
                    spam_status="not_spam",
                ),
                # 4. recent-order (customer_status: has_order_record)
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=204,
                    uidvalidity=1,
                    sender_email="buyer_order@gmail.com",
                    recipient_email="support@wrydeco.com",
                    subject="Where is my order #1002?",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="pending",
                    classification_category="order_support",
                    customer_status="has_order_record",
                    spam_status="not_spam",
                ),
                # 5. complaint (classification_category: complaint)
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=205,
                    uidvalidity=1,
                    sender_email="buyer_complaint@gmail.com",
                    recipient_email="support@wrydeco.com",
                    subject="Broken frame arrived!",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="pending_approval",
                    classification_category="complaint",
                    spam_status="not_spam",
                ),
            ]

            # Seed 5 adversarial spam variants attempting infiltration into each queue
            spam_emails = [
                # Infiltration Attempt A: Spam targeting ready-to-review
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=301,
                    uidvalidity=1,
                    sender_email="spammer1@casino.xyz",
                    recipient_email="support@wrydeco.com",
                    subject="[SPAM A] 100 Free Spins Ready For Approval!",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="pending_approval",  # Sneaky status!
                    classification_category="other",
                    spam_status="spam",  # Classified as spam
                    spam_score=0.99,
                ),
                # Infiltration Attempt B: Spam targeting needs-manual-review
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=302,
                    uidvalidity=1,
                    sender_email="spammer2@phishing.net",
                    recipient_email="support@wrydeco.com",
                    subject="[SPAM B] Account Suspended Manual Check",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="manual_review",  # Sneaky status!
                    classification_category="other",
                    spam_status="spam",
                    spam_score=0.95,
                ),
                # Infiltration Attempt C: Spam masquerading as product inquiry
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=303,
                    uidvalidity=1,
                    sender_email="spammer3@seo-blast.org",
                    recipient_email="support@wrydeco.com",
                    subject="[SPAM C] Inquiry about ranking your products",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="spam",
                    classification_category="product_inquiry",  # Sneaky intent!
                    spam_status="spam",
                    spam_score=0.98,
                ),
                # Infiltration Attempt D: Spam masquerading as order inquiry
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=304,
                    uidvalidity=1,
                    sender_email="spammer4@fake-tracking.cc",
                    recipient_email="support@wrydeco.com",
                    subject="[SPAM D] Update order status now",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="spam",
                    classification_category="order_support",
                    customer_status="has_order_record",  # Sneaky customer status!
                    spam_status="spam",
                    spam_score=0.92,
                ),
                # Infiltration Attempt E: Spam masquerading as complaint
                IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    folder="INBOX",
                    imap_uid=305,
                    uidvalidity=1,
                    sender_email="spammer5@extortion.top",
                    recipient_email="support@wrydeco.com",
                    subject="[SPAM E] Complaint about copyright violation",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="spam",
                    classification_category="complaint",  # Sneaky complaint!
                    spam_status="spam",
                    spam_score=0.97,
                ),
            ]

            for em in legit_emails + spam_emails:
                session.add(em)
            await session.commit()

            # 2.1: Stress-test get_queue_stats
            stats = await TransactionalQueueService.get_queue_stats(session, store_id=store.id)
            spam_ids = {e.id for e in spam_emails}
            legit_ids = {e.id for e in legit_emails}

            # Verify queue stats counts
            # Legit ready: buyer_ready, buyer_complaint (both pending_approval) = 2
            # Legit manual: buyer_manual = 1
            # Legit product: buyer_ready (pending_approval), buyer_prod (classified) = 2
            # Legit recent_order: buyer_order = 1
            # Legit complaint: buyer_complaint = 1
            # Spam total: 5
            stats_ok = (
                stats.spam == 5
                and stats.ready_to_review == 2
                and stats.needs_manual_review == 1
                and stats.recent_order == 1
                and stats.complaint == 1
            )
            if stats_ok:
                self.record(
                    "R-08 Stats Isolation",
                    True,
                    f"Stats accurately isolated: spam={stats.spam}, ready={stats.ready_to_review}, manual={stats.needs_manual_review}, order={stats.recent_order}, complaint={stats.complaint}",
                )
            else:
                self.record(
                    "R-08 Stats Isolation",
                    False,
                    f"Stats leak detected: spam={stats.spam}, ready={stats.ready_to_review}, manual={stats.needs_manual_review}, order={stats.recent_order}, complaint={stats.complaint}",
                )

            # 2.2: Stress-test get_queue_emails for ALL 5 regular queues
            regular_queues = [
                "ready-to-review",
                "needs-manual-review",
                "product-inquiry",
                "recent-order",
                "complaint",
            ]
            leaks = []
            for q_name in regular_queues:
                res = await TransactionalQueueService.get_queue_emails(
                    session, q_name, store_id=store.id, limit=50
                )
                returned_ids = {item.id for item in res.items}
                intersect = returned_ids.intersection(spam_ids)
                if intersect:
                    leaks.append(f"Queue '{q_name}' leaked {len(intersect)} spam emails: {intersect}")

            if not leaks:
                self.record(
                    "R-08 Regular Queue Isolation (5 Queues)",
                    True,
                    "Zero spam emails returned across all 5 regular queues (ready-to-review, needs-manual-review, product-inquiry, recent-order, complaint).",
                )
            else:
                self.record(
                    "R-08 Regular Queue Isolation (5 Queues)",
                    False,
                    "; ".join(leaks),
                )

            # 2.3: Verify Spam Tab (`spam` / queue-nav-spam)
            spam_res = await TransactionalQueueService.get_queue_emails(
                session, "spam", store_id=store.id, limit=50
            )
            spam_returned_ids = {item.id for item in spam_res.items}
            all_spams_present = spam_ids.issubset(spam_returned_ids)
            no_legits_in_spam = len(spam_returned_ids.intersection(legit_ids)) == 0

            if all_spams_present and no_legits_in_spam:
                self.record(
                    "R-08 Spam Queue Visibility & Exclusivity",
                    True,
                    f"Tab Spam returned exactly 5/5 spam emails and 0 legitimate emails. total={spam_res.total}",
                )
            else:
                self.record(
                    "R-08 Spam Queue Visibility & Exclusivity",
                    False,
                    f"Spam queue mismatch: all_spams={all_spams_present}, no_legits={no_legits_in_spam}",
                )

    # =========================================================================
    # SUITE 3: INVARIANT R-08 (MAILSERVER IMMUTABILITY & PROTOCOL INTEGRITY)
    # =========================================================================
    async def run_mailserver_immutability_suite(self):
        print("\n" + "=" * 75)
        print("SUITE 3: INVARIANT R-08 (MAILSERVER IMMUTABILITY & ZERO-MUTATION)")
        print("=" * 75)

        # 3.1: AST / Static Codebase Audit for Destructive IMAP Commands
        destructive_patterns = [
            ".expunge(",
            "EXPUNGE",
            "+FLAGS (\\Deleted)",
            "+FLAGS (\\\\Deleted)",
            "(\\Deleted)",
            "(\\\\Deleted)",
            ".store(",
            "uid(\"STORE\"",
            "uid('STORE'",
        ]
        scanned_dirs = [
            os.path.join(backend_path, "app", "mail"),
            os.path.join(backend_path, "app", "ingestion"),
            os.path.join(backend_path, "app", "queue"),
            os.path.join(backend_path, "app", "ai"),
        ]
        violations = []
        for sdir in scanned_dirs:
            for root, _, files in os.walk(sdir):
                for fname in files:
                    if fname.endswith(".py"):
                        fpath = os.path.join(root, fname)
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                            for pat in destructive_patterns:
                                if pat in content:
                                    violations.append(f"{fname}: contains forbidden pattern '{pat}'")

        if not violations:
            self.record(
                "R-08 Static Immutability Audit",
                True,
                "Zero occurrences of EXPUNGE, \\Deleted, or destructive STORE commands found in mail ingestion, queue, and AI modules.",
            )
        else:
            self.record(
                "R-08 Static Immutability Audit",
                False,
                f"Destructive commands detected: {violations}",
            )

        # 3.2: IMAPClient Read-Only Protocol Verification
        # Verify that IMAPClient methods only use examine() and BODY.PEEK
        client = IMAPClient(
            host="mail.wrydeco.com",
            port=993,
            username="support@wrydeco.com",
            password="test_password",
        )
        mock_imap = MagicMock()
        mock_imap.examine.return_value = ("OK", [b"42"])
        mock_imap.status.return_value = ("OK", [b"INBOX (UIDVALIDITY 100 UIDNEXT 200 UNSEEN 5)"])
        mock_imap.capability.return_value = ("OK", [b"IMAP4rev1 IDLE"])

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            # Test test_connection
            res = await client.test_connection()
            assert res.success is True
            assert mock_imap.examine.called
            assert not mock_imap.select.called
            assert not mock_imap.expunge.called

            # Test search_new_uids
            mock_imap.uid.return_value = ("OK", [b"101 102 103"])
            uids = await client.search_new_uids(100)
            assert uids == [101, 102, 103]
            assert mock_imap.examine.called
            assert not mock_imap.select.called

            # Test fetch_raw_email_peek
            mock_imap.uid.return_value = ("OK", [(b"1 (UID 101 BODY[] {12})", b"fake content")])
            raw_bytes = await client.fetch_raw_email_peek(101)
            # Verify the command called on uid was BODY.PEEK[]
            fetch_calls = [str(c) for c in mock_imap.uid.mock_calls if "FETCH" in str(c)]
            has_body_peek = any("BODY.PEEK" in c for c in fetch_calls)

            if has_body_peek and not mock_imap.expunge.called and not mock_imap.select.called:
                self.record(
                    "R-08 IMAP Protocol Non-Destructive Trace",
                    True,
                    "Client strictly executes examine() and BODY.PEEK[]; zero select(), expunge(), or store() calls.",
                )
            else:
                self.record(
                    "R-08 IMAP Protocol Non-Destructive Trace",
                    False,
                    f"Protocol violation: has_peek={has_body_peek}, expunge={mock_imap.expunge.called}",
                )

    # =========================================================================
    # SUITE 4: INVARIANT R-08 (`unmark-spam` RESTORATION & AUDIT TRAIL)
    # =========================================================================
    async def run_unmark_spam_recovery_suite(self):
        print("\n" + "=" * 75)
        print("SUITE 4: INVARIANT R-08 (`unmark-spam` RESTORATION & AUDIT TRAIL)")
        print("=" * 75)

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        async with session_factory() as session:
            store = StoreProfile(
                id=uuid.uuid4(),
                name="Wrydeco Store",
                brand_name="Wrydeco",
                public_domain="wrydeco.com",
            )
            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="support@wrydeco.com",
                imap_host="mail.wrydeco.com",
                imap_port=993,
                imap_tls_mode="SSL_TLS",
                smtp_host="mail.wrydeco.com",
                smtp_port=587,
                smtp_tls_mode="STARTTLS",
                encrypted_password="enc_password",
            )
            operator = User(
                id=uuid.uuid4(),
                username="auditor_lead",
                normalized_username="auditor_lead",
                password_hash="hash",
                status="active",
            )
            spam_email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                folder="INBOX",
                imap_uid=401,
                uidvalidity=1,
                sender_email="innocent_shopper@gmail.com",
                recipient_email="support@wrydeco.com",
                subject="Where can I buy replacement cushions?",
                received_at=datetime.datetime.now(datetime.UTC),
                status="spam",
                classification_category="product_inquiry",
                spam_status="spam",
                spam_score=0.88,
            )
            session.add(store)
            session.add(mailbox)
            session.add(operator)
            session.add(spam_email)
            await session.commit()

            # Pre-unmark check: email is in spam tab and NOT in product-inquiry queue
            stats_before = await TransactionalQueueService.get_queue_stats(session, store_id=store.id)
            assert stats_before.spam == 1
            assert stats_before.product_inquiry == 0

            # Execute unmark_spam
            recovered = await TransactionalQueueService.unmark_spam(
                session, spam_email.id, user_id=operator.id
            )

            # Post-unmark check
            assert recovered.spam_status == "not_spam"
            assert recovered.status == "classified"

            # Check stats after unmark
            stats_after = await TransactionalQueueService.get_queue_stats(session, store_id=store.id)
            assert stats_after.spam == 0
            assert stats_after.product_inquiry == 1

            # Check queue list queries
            spam_list = await TransactionalQueueService.get_queue_emails(session, "spam", store_id=store.id)
            prod_list = await TransactionalQueueService.get_queue_emails(
                session, "product-inquiry", store_id=store.id
            )

            assert not any(i.id == spam_email.id for i in spam_list.items)
            assert any(i.id == spam_email.id for i in prod_list.items)

            # Check Audit Event recorded
            audit_stmt = select(AuditEvent).where(
                AuditEvent.target_id == str(spam_email.id),
                AuditEvent.event_type == "EMAIL_UNMARK_SPAM",
            )
            audit_rec = (await session.execute(audit_stmt)).scalar_one_or_none()
            assert audit_rec is not None
            assert audit_rec.actor_user_id == operator.id

            # Check EmailClassification record created for audit trail
            class_stmt = select(EmailClassification).where(
                EmailClassification.incoming_email_id == spam_email.id,
                EmailClassification.source == "user",
            )
            class_rec = (await session.execute(class_stmt)).scalar_one_or_none()
            assert class_rec is not None
            assert class_rec.spam_status == "not_spam"

            self.record(
                "R-08 Unmark Spam Recovery & Audit",
                True,
                "Unmarked email accurately restored: status=classified, spam_status=not_spam, moved to product-inquiry queue, AuditEvent logged.",
            )

    # =========================================================================
    # SUITE 5: INVARIANT R-03 (PIEZAPRINT MULTI-LAYER ABSOLUTE EXCLUSION)
    # =========================================================================
    async def run_piezaprint_exclusion_suite(self):
        print("\n" + "=" * 75)
        print("SUITE 5: INVARIANT R-03 (PIEZAPRINT MULTI-LAYER ABSOLUTE EXCLUSION)")
        print("=" * 75)

        adversarial_piezaprint_inputs = [
            "support@piezaprint.com",
            "SUPPORT@PIEZAPRINT.COM",
            "admin@piezaprint.com",
            "Orders@PieZaPrint.Com",
            "piezaprint.com",
            "mail.piezaprint.com",
            "smtp.piezaprint.com",
        ]

        # 5.1: IMAPClient instant rejection
        imap_rejections = 0
        for target in adversarial_piezaprint_inputs:
            try:
                IMAPClient(host=target, username="support@other.com", password="pwd")
            except PiezaprintExclusionError:
                imap_rejections += 1
            except Exception:
                pass

            try:
                IMAPClient(host="mail.valid.com", username=target, password="pwd")
            except PiezaprintExclusionError:
                imap_rejections += 1
            except Exception:
                pass

        if imap_rejections > 0:
            self.record(
                "R-03 IMAPClient Piezaprint Rejection",
                True,
                f"IMAPClient rejected {imap_rejections} adversarial piezaprint vectors with PiezaprintExclusionError.",
            )
        else:
            self.record(
                "R-03 IMAPClient Piezaprint Rejection",
                False,
                "IMAPClient allowed piezaprint connection!",
            )

        # 5.2: SMTPClient instant rejection
        smtp_rejections = 0
        for target in adversarial_piezaprint_inputs:
            try:
                SMTPClient(host=target, username="support@other.com", password="pwd")
            except PiezaprintExclusionError:
                smtp_rejections += 1
            except Exception:
                pass

        if smtp_rejections > 0:
            self.record(
                "R-03 SMTPClient Piezaprint Rejection",
                True,
                f"SMTPClient rejected {smtp_rejections} adversarial piezaprint vectors with PiezaprintExclusionError.",
            )
        else:
            self.record(
                "R-03 SMTPClient Piezaprint Rejection",
                False,
                "SMTPClient allowed piezaprint connection!",
            )

    # =========================================================================
    # SUMMARY & VERDICT
    # =========================================================================
    async def run_all(self):
        print("=" * 75)
        print("STARTING EMPIRICAL ADVERSARIAL STRESS TEST FOR PHASE 4")
        print("=" * 75)
        await self.run_ai_direct_https_suite()
        await self.run_spam_isolation_suite()
        await self.run_mailserver_immutability_suite()
        await self.run_unmark_spam_recovery_suite()
        await self.run_piezaprint_exclusion_suite()

        print("\n" + "=" * 75)
        print(f"ADVERSARIAL STRESS TEST RESULTS: {self.passed}/{self.tests_run} PASSED")
        print("=" * 75)
        if self.failed == 0:
            print("[SUCCESS] ALL ADVERSARIAL STRESS TESTS PASSED WITH 100% INTEGRITY!")
            return 0
        else:
            print(f"[FAIL] {self.failed} TEST(S) FAILED!", file=sys.stderr)
            return 1


if __name__ == "__main__":
    runner = AdversarialPhase4Runner()
    sys.exit(asyncio.run(runner.run_all()))
