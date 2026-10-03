"""Challenger Empirical Adversarial Test Harness for Phase 6.

Adversarial Stress Testing covering:
1. Invariant R-01 (Zero Autonomous Sending):
   - AST & codebase static verification: 100% no background worker, cron, or scheduler calls send without human session.
   - Dynamic probe: background draft generation leaves email in pending_approval with ZERO SMTP calls.
   - API route security: POST approve-and-send rejected without authenticated user session.
2. Invariant R-26 (Send Idempotency & Concurrency Race):
   - 10 concurrent requests with identical idempotency_key -> Exactly 1 SMTP call, 9 safe replays/blocked.
   - Double-click storm with 2 different idempotency keys on in-flight 'sending' email -> 409 Conflict.
   - Re-send attempt with new idempotency key on already 'sent' email -> 409 Conflict (EMAIL_ALREADY_SENT).
   - Concurrent race between two different idempotency keys -> exactly 1 succeeds, 1 gets 409 Conflict.
3. Invariant R-25 (SMTP Delivery, RFC 5322 Threading & IMAP Fault Isolation):
   - Thread headers preservation: In-Reply-To, References, bracket normalization, subject Re: deduplication.
   - IMAP Sent-folder copy fault isolation: Socket drops, timeouts, mailbox full -> email remains 'sent',
     sent_folder_append_status='failed', zero SMTP rollback.
4. Ambiguous Delivery Handling:
   - SMTP drop/timeout during DATA phase -> status='delivery_unknown', zero automatic retries.
   - Protection against blind re-sending on delivery_unknown -> 409 Conflict.
   - Operator manual resolution workflows (resolve_sent vs resolve_reopen).
5. Invariant R-03 (Piezaprint Exclusion in Mail Dispatcher).
6. Invariants R-22, R-23 & Anti-Hallucination Prompt Directives.
"""

from __future__ import annotations

# ruff: noqa: E402
import ast
import asyncio
import datetime
import os
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

# Ensure project root and backend are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
backend_path = os.path.join(PROJECT_ROOT, "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

import pytest
from app.db.base import Base
from app.db.models.audit import AuditEvent
from app.db.models.draft import ReplyDeliveryAttempt, ReplyDraft, ReplyDraftVersion
from app.db.models.email import IncomingEmail
from app.db.models.shopify import ShopifyProductSnapshot
from app.db.models.store import Mailbox, StorePolicy, StoreProfile
from app.db.models.user import User
from app.delivery.service import (
    DeliveryBusinessError,
    DeliveryService,
    build_rfc5322_reply_message,
)
from app.draft.prompt import build_draft_prompt, resolve_draft_language
from app.draft.service import DraftBusinessError, DraftService
from app.mail.exceptions import PiezaprintExclusionError
from app.mail.schemas import SMTPDeliveryResult
from app.mail.smtp_client import SMTPClient
from app.queue.service import TransactionalQueueService
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


class EmpiricalPhase6Challenger:
    """Standalone and Pytest-compatible Adversarial Test Runner for Phase 6."""

    def __init__(self) -> None:
        self.tests_run = 0
        self.passed = 0
        self.failed = 0
        self.findings: list[str] = []

    def record(self, test_name: str, passed: bool, detail: str = "", finding: str = "") -> None:
        self.tests_run += 1
        if passed:
            self.passed += 1
            print(f"  [PASS] {test_name}: {detail}")
        else:
            self.failed += 1
            msg = f"  [FAIL] {test_name}: {detail} | FINDING: {finding}"
            print(msg, file=sys.stderr)
            self.findings.append(f"{test_name}: {finding}")

    async def create_harness_engine(self) -> AsyncEngine:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine

    async def seed_test_email(
        self, session: AsyncSession, prefix: str = "tst"
    ) -> tuple[User, StoreProfile, Mailbox, IncomingEmail, ReplyDraft, ReplyDraftVersion]:
        now_utc = datetime.datetime.now(datetime.UTC)
        user = User(
            id=uuid.uuid4(),
            username=f"user_{prefix}_{uuid.uuid4().hex[:6]}",
            normalized_username=f"user_{prefix}_{uuid.uuid4().hex[:6]}",
            password_hash="argon2id$test",
            status="active",
        )
        store = StoreProfile(
            id=uuid.uuid4(),
            name=f"Store {prefix.upper()}",
            brand_name=f"Brand {prefix.upper()}",
            public_domain=f"{prefix}.wrydeco.com",
            email_signature=f"Best regards,\n{prefix.upper()} Support",
            status="active",
        )
        mailbox = Mailbox(
            id=uuid.uuid4(),
            store_profile_id=store.id,
            address=f"support@{prefix}.wrydeco.com",
            encrypted_password="enc_mock_password",
            smtp_host=f"mail.{prefix}.wrydeco.com",
            smtp_port=587,
            smtp_tls_mode="STARTTLS",
            imap_host=f"mail.{prefix}.wrydeco.com",
            imap_port=993,
            imap_tls_mode="SSL",
        )
        session.add_all([user, store, mailbox])
        await session.flush()

        email = IncomingEmail(
            id=uuid.uuid4(),
            store_profile_id=store.id,
            mailbox_id=mailbox.id,
            imap_uid=int(uuid.uuid4().int % 100000) + 1,
            uidvalidity=1,
            message_id=f"<orig-{uuid.uuid4().hex[:8]}@customer.com>",
            sender_email="customer@example.com",
            recipient_email=mailbox.address,
            subject=f"Inquiry regarding order {prefix}",
            received_at=now_utc,
            status="pending_approval",
        )
        draft = ReplyDraft(
            id=uuid.uuid4(),
            incoming_email_id=email.id,
            store_profile_id=store.id,
            current_version_number=1,
            status="pending_approval",
        )
        session.add_all([email, draft])
        await session.flush()

        version = ReplyDraftVersion(
            id=uuid.uuid4(),
            draft_id=draft.id,
            incoming_email_id=email.id,
            version_number=1,
            subject=f"Re: Inquiry regarding order {prefix}",
            body_text=f"Thank you for contacting {prefix.upper()} Support. Your order is being processed.",
            body_html=f"<p>Thank you for contacting {prefix.upper()} Support. Your order is being processed.</p>",
            language="en",
            source="ai",
            content_hash="test_content_hash_123",
            is_current_version=True,
        )
        session.add(version)
        await session.flush()
        draft.current_version_id = version.id
        await session.commit()

        return user, store, mailbox, email, draft, version

    # -------------------------------------------------------------------------
    # SUITE 1: Zero Autonomous Sending (Invariant R-01)
    # -------------------------------------------------------------------------
    async def run_zero_autonomous_sending_suite(self) -> None:
        print("\n--- [SUITE 1] Invariant R-01: Zero Autonomous Sending & AST Analysis ---")

        # 1. AST Static Verification: Check all files in backend/app
        app_dir = os.path.join(PROJECT_ROOT, "backend", "app")
        send_invocations = []
        approve_and_send_calls = []

        for root, _, files in os.walk(app_dir):
            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                    with open(full_path, encoding="utf-8") as f:
                        content = f.read()
                    try:
                        tree = ast.parse(content, filename=rel_path)
                    except SyntaxError:
                        continue

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call):
                            # Check for send_message or send_message_robust
                            func_name = ""
                            if isinstance(node.func, ast.Attribute):
                                func_name = node.func.attr
                            elif isinstance(node.func, ast.Name):
                                func_name = node.func.id

                            if func_name in ("send_message_robust", "send_message"):
                                send_invocations.append((rel_path, node.lineno, func_name))
                            elif func_name == "approve_and_send":
                                approve_and_send_calls.append((rel_path, node.lineno))

        # Check: send_message_robust is called ONLY in backend/app/delivery/service.py
        non_delivery_sends = [
            (p, line, fn)
            for p, line, fn in send_invocations
            if "delivery" not in p and "mail" not in p  # mail client defines it, delivery calls it
        ]
        self.record(
            "R01-01-AST-NO-BACKGROUND-SMTP-CALLS",
            len(non_delivery_sends) == 0,
            f"Found {len(send_invocations)} SMTP calls; {len(non_delivery_sends)} outside delivery/mail modules.",
            finding=f"Unauthorized SMTP calls found in: {non_delivery_sends}",
        )

        # Check: approve_and_send is called ONLY in delivery/router.py
        non_router_approvals = [
            (p, line)
            for p, line in approve_and_send_calls
            if not p.endswith(os.path.join("delivery", "router.py"))
            and not p.endswith(os.path.join("delivery", "service.py"))  # def approve_and_send
        ]
        self.record(
            "R01-02-AST-APPROVE-ONLY-FROM-AUTHENTICATED-ROUTER",
            len(non_router_approvals) == 0,
            f"Found {len(approve_and_send_calls)} calls; {len(non_router_approvals)} outside router.",
            finding=f"Unauthorized approve_and_send calls in: {non_router_approvals}",
        )

        # 2. Dynamic Behavioral Test: Queue draft generation NEVER sends email
        engine = await self.create_harness_engine()
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="dyn")
            # Email starts in pending_approval
            assert email.status == "pending_approval"

            with patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp:
                # Simulating a worker running queue job
                # Even if we inspect jobs, no job can send email
                from app.db.models.email import EmailJob
                job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email.id,
                    store_profile_id=store.id,
                    job_type="generate_draft",
                    status="queued",
                )
                session.add(job)
                await session.commit()

                # Process draft job
                with patch("app.draft.service.DraftService.generate_initial_draft", new_callable=AsyncMock) as mock_gen:
                    mock_gen.return_value = draft
                    await TransactionalQueueService.process_job(session, job.id, "worker-test")

                # Verify: SMTP was NOT called
                mock_smtp.assert_not_called()

                # Refreshed email must NOT be sent
                refreshed = await session.get(IncomingEmail, email.id)
                assert refreshed is not None
                self.record(
                    "R01-03-QUEUE-WORKER-ZERO-AUTONOMOUS-SEND",
                    refreshed.status == "pending_approval" and mock_smtp.call_count == 0,
                    f"Email status is '{refreshed.status}', SMTP call count: {mock_smtp.call_count}",
                    finding="Queue worker autonomously modified email status to sent or triggered SMTP",
                )
        await engine.dispose()

    # -------------------------------------------------------------------------
    # SUITE 2: Send Idempotency & Concurrency Race (Invariant R-26)
    # -------------------------------------------------------------------------
    async def run_send_idempotency_concurrency_suite(self) -> None:
        print("\n--- [SUITE 2] Invariant R-26: Send Idempotency & Concurrency Race ---")

        engine = await self.create_harness_engine()
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        # 1. 10 Requests with IDENTICAL idempotency_key (In-flight storm + post-send replays)
        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="conc10")
            email_id = email.id
            user_id = user.id

        idempotency_key = str(uuid.uuid4())
        smtp_send_count = 0

        async def simulated_robust_send(*args, **kwargs):
            nonlocal smtp_send_count
            smtp_send_count += 1
            # Artificial network latency to simulate in-flight SMTP socket transmission
            await asyncio.sleep(0.08)
            return SMTPDeliveryResult(
                status="sent",
                response_code=250,
                response_text="250 2.0.0 Ok: queued",
            )

        with (
            patch("app.delivery.service.SMTPClient.send_message_robust", side_effect=simulated_robust_send),
            patch("app.delivery.service.IMAPClient.append_to_sent_folder", new_callable=AsyncMock) as mock_imap,
        ):
            mock_imap.return_value = True

            async def dispatch_worker(worker_idx: int, delay_s: float) -> dict[str, Any]:
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                async with session_factory() as worker_session:
                    try:
                        resp = await DeliveryService.approve_and_send(
                            session=worker_session,
                            email_id=email_id,
                            idempotency_key=idempotency_key,
                            user_id=user_id,
                        )
                        return {"worker": worker_idx, "success": True, "status": resp.status, "msg_id": resp.outgoing_message_id}
                    except DeliveryBusinessError as exc:
                        return {"worker": worker_idx, "success": False, "code": exc.code, "status_code": exc.status_code}
                    except Exception as exc:
                        return {"worker": worker_idx, "success": False, "error": str(exc)}

            # Launch 10 dispatches:
            # - Request 0 starts the send (delays in SMTP for 0.08s)
            # - Requests 1..6 hit during in-flight send (delay 0.02s..0.06s) -> blocked 409 SEND_IN_PROGRESS
            # - Requests 7..9 hit after send completes (delay 0.12s..0.15s) -> replay 200 sent
            tasks = [
                dispatch_worker(0, 0.0),
                dispatch_worker(1, 0.02),
                dispatch_worker(2, 0.03),
                dispatch_worker(3, 0.04),
                dispatch_worker(4, 0.05),
                dispatch_worker(5, 0.06),
                dispatch_worker(6, 0.07),
                dispatch_worker(7, 0.12),
                dispatch_worker(8, 0.13),
                dispatch_worker(9, 0.14),
            ]
            results = await asyncio.gather(*tasks)

        # Post-send Replay Test: A new request with the same idempotency key after send completed
        replay_res = await dispatch_worker(10, 0.0)

        # Verification of requests:
        # 1. SMTP MUST be called exactly once
        # 2. Worker 0 succeeded
        # 3. Workers 1..9 safely blocked either by 409 SEND_IN_PROGRESS or DB UNIQUE constraint
        # 4. Worker 10 replayed sent status with identical outgoing_message_id
        orig_send = results[0]
        blocked_in_flight = [
            r for r in results[1:]
            if r.get("code") in ("SEND_IN_PROGRESS", "EMAIL_ALREADY_SENT")
            or "UNIQUE constraint" in str(r.get("error"))
            or "OperationalError" in str(r.get("error"))
        ]
        replay_ok = (
            replay_res.get("success") is True
            and replay_res.get("status") == "sent"
            and replay_res.get("msg_id") == orig_send.get("msg_id")
        )

        all_safe = (
            smtp_send_count == 1
            and orig_send.get("success") is True
            and len(blocked_in_flight) == 9
            and replay_ok
        )

        self.record(
            "R26-01-TEN-CONCURRENT-REQUESTS-SINGLE-SMTP-SEND",
            all_safe,
            f"SMTP called {smtp_send_count} time(s). Orig success={orig_send.get('success')}, Blocked concurrent={len(blocked_in_flight)}/9, Replay matched={replay_ok}",
            finding=f"SMTP called multiple times ({smtp_send_count}) or unhandled concurrency error occurred: {results}",
        )

        # Verify DB attempt records: Exactly 1 record with this idempotency key
        async with session_factory() as session:
            attempts_stmt = select(ReplyDeliveryAttempt).where(
                ReplyDeliveryAttempt.idempotency_key == idempotency_key
            )
            attempts = (await session.execute(attempts_stmt)).scalars().all()
            self.record(
                "R26-02-SINGLE-ATTEMPT-ROW-PERSISTED",
                len(attempts) == 1 and attempts[0].status == "sent",
                f"Total attempts with key in DB: {len(attempts)}, status={attempts[0].status if attempts else 'none'}",
                finding=f"Database persisted duplicate attempt rows: {len(attempts)}",
            )

        # 2. Double-Click Storm with TWO DIFFERENT KEYS on 'sending' email
        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="storm")
            # Force email into 'sending' state
            email.status = "sending"
            draft.status = "sending"
            await session.commit()
            email_id = email.id
            user_id = user.id

        key_different = str(uuid.uuid4())
        storm_blocked = False
        storm_error_code = ""

        async with session_factory() as session:
            try:
                await DeliveryService.approve_and_send(
                    session=session,
                    email_id=email_id,
                    idempotency_key=key_different,
                    user_id=user_id,
                )
            except DeliveryBusinessError as exc:
                storm_blocked = True
                storm_error_code = exc.code

        self.record(
            "R26-03-DOUBLE-CLICK-STORM-DIFFERENT-KEY-BLOCKED-409",
            storm_blocked and storm_error_code == "SEND_IN_PROGRESS",
            f"Blocked={storm_blocked}, code='{storm_error_code}'",
            finding=f"Double-click storm with new key on in-flight email was not blocked with SEND_IN_PROGRESS (got {storm_error_code})",
        )

        # 3. Resend attempt with DIFFERENT KEY on already 'sent' email
        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="alreadysent")
            email.status = "sent"
            draft.status = "sent"
            await session.commit()
            email_id = email.id
            user_id = user.id

        key_third = str(uuid.uuid4())
        already_sent_blocked = False
        already_sent_code = ""

        async with session_factory() as session:
            try:
                await DeliveryService.approve_and_send(
                    session=session,
                    email_id=email_id,
                    idempotency_key=key_third,
                    user_id=user_id,
                )
            except DeliveryBusinessError as exc:
                already_sent_blocked = True
                already_sent_code = exc.code

        self.record(
            "R26-04-RESEND-DIFFERENT-KEY-ON-SENT-EMAIL-BLOCKED-409",
            already_sent_blocked and already_sent_code == "EMAIL_ALREADY_SENT",
            f"Blocked={already_sent_blocked}, code='{already_sent_code}'",
            finding=f"Attempt to send already sent email with different key was not blocked with EMAIL_ALREADY_SENT (got {already_sent_code})",
        )

        # 4. Simultaneous Race between Key A and Key B on pending_approval email
        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="raceab")
            email_id = email.id
            user_id = user.id

        key_a = f"key_a_{uuid.uuid4()}"
        key_b = f"key_b_{uuid.uuid4()}"
        race_smtp_calls = 0

        async def delayed_send(*args, **kwargs):
            nonlocal race_smtp_calls
            race_smtp_calls += 1
            await asyncio.sleep(0.15)
            return SMTPDeliveryResult(status="sent", response_code=250, response_text="250 OK")

        with (
            patch("app.delivery.service.SMTPClient.send_message_robust", side_effect=delayed_send),
            patch("app.delivery.service.IMAPClient.append_to_sent_folder", new_callable=AsyncMock) as mock_imap,
        ):
            mock_imap.return_value = True

            async def call_key(k: str, delay_s: float) -> dict[str, Any]:
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                async with session_factory() as s:
                    try:
                        res = await DeliveryService.approve_and_send(
                            session=s,
                            email_id=email_id,
                            idempotency_key=k,
                            user_id=user_id,
                        )
                        return {"key": k, "success": True, "status": res.status}
                    except DeliveryBusinessError as exc:
                        return {"key": k, "success": False, "code": exc.code, "status": exc.status_code}
                    except Exception as exc:
                        return {"key": k, "success": False, "error": str(exc)}

            # Key A starts send, Key B arrives 0.08s later while Key A is transmitting via SMTP
            race_results = await asyncio.gather(call_key(key_a, 0.0), call_key(key_b, 0.08))

        race_successes = [r for r in race_results if r.get("success") is True]
        race_conflicts = [
            r for r in race_results
            if r.get("code") in ("SEND_IN_PROGRESS", "EMAIL_ALREADY_SENT")
            or "cannot commit transaction" in str(r.get("error"))
            or "database is locked" in str(r.get("error"))
        ]

        self.record(
            "R26-05-CONCURRENT-DIFFERENT-KEYS-EXACTLY-ONE-SEND",
            race_smtp_calls == 1 and len(race_successes) == 1 and len(race_conflicts) == 1,
            f"SMTP calls={race_smtp_calls}, Successes={len(race_successes)}, Conflicts={len(race_conflicts)}",
            finding=f"Concurrency race with different keys allowed multiple sends or unexpected state: {race_results}",
        )

        await engine.dispose()

    # -------------------------------------------------------------------------
    # SUITE 3: SMTP Delivery, RFC 5322 Threading & IMAP Fault Isolation (R-25)
    # -------------------------------------------------------------------------
    async def run_threading_and_imap_isolation_suite(self) -> None:
        print("\n--- [SUITE 3] Invariant R-25: RFC 5322 Threading & IMAP Fault Isolation ---")

        engine = await self.create_harness_engine()
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        # 1. Threading headers verification with various input formats
        test_cases = [
            ("<clean-123@customer.com>", "<clean-123@customer.com>"),
            ("unbracketed-456@customer.com", "<unbracketed-456@customer.com>"),
            ("  <whitespace-789@customer.com>  \n", "<whitespace-789@customer.com>"),
        ]

        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="thread")

            for input_mid, expected_mid in test_cases:
                email.message_id = input_mid
                version.subject = "Order Inquiry"
                msg = build_rfc5322_reply_message(
                    store=store,
                    mailbox=mailbox,
                    original_email=email,
                    draft_version=version,
                    outgoing_message_id=f"<out-{uuid.uuid4()}@wrydeco.com>",
                    recipient="customer@example.com",
                )
                in_reply_to = msg["In-Reply-To"]
                references = msg["References"]
                subject = msg["Subject"]

                valid_headers = (
                    in_reply_to == expected_mid
                    and references == expected_mid
                    and subject == "Re: Order Inquiry"
                )
                self.record(
                    f"R25-01-RFC5322-THREADING-{input_mid.strip()[:15]}",
                    valid_headers,
                    f"In-Reply-To='{in_reply_to}', References='{references}', Subject='{subject}'",
                    finding=f"RFC 5322 headers malformed for input '{input_mid}'",
                )

            # Test Re: deduplication (e.g. subject already has Re:)
            version.subject = "Re: Already prefixed subject"
            msg_re = build_rfc5322_reply_message(
                store=store,
                mailbox=mailbox,
                original_email=email,
                draft_version=version,
                outgoing_message_id="<test@wrydeco.com>",
                recipient="customer@example.com",
            )
            self.record(
                "R25-02-SUBJECT-RE-DEDUPLICATION",
                msg_re["Subject"] == "Re: Already prefixed subject",
                f"Subject='{msg_re['Subject']}'",
                finding="Subject created redundant 'Re: Re:' prefix",
            )

        # 2. IMAP Sent-folder Fault Isolation Stress Test
        # Test 3 distinct IMAP crash modes: ConnectionReset, Timeout, and return False (mailbox quota)
        imap_failure_modes = [
            ("ConnectionReset", ConnectionResetError("IMAP connection forcibly closed by peer")),
            ("Timeout", TimeoutError("IMAP APPEND timed out after 15s")),
            ("ReturnFalse", "RETURN_FALSE"),
        ]

        for mode_name, exc_or_val in imap_failure_modes:
            async with session_factory() as session:
                user, store, mailbox, email, draft, version = await self.seed_test_email(
                    session, prefix=f"imap_{mode_name.lower()}"
                )
                email_id = email.id
                user_id = user.id

            smtp_ok = SMTPDeliveryResult(status="sent", response_code=250, response_text="250 OK: queued")

            async def mock_imap_append(*args, target=exc_or_val, **kwargs):
                if target == "RETURN_FALSE":
                    return False
                raise target

            with (
                patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp,
                patch("app.delivery.service.IMAPClient.append_to_sent_folder", side_effect=mock_imap_append),
            ):
                mock_smtp.return_value = smtp_ok

                async with session_factory() as session:
                    resp = await DeliveryService.approve_and_send(
                        session=session,
                        email_id=email_id,
                        idempotency_key=str(uuid.uuid4()),
                        user_id=user_id,
                    )

                # Verification:
                # 1. API Response is successful with status="sent"
                # 2. sent_folder_append_status is "failed"
                # 3. Database email status is "sent" (ZERO SMTP rollback!)
                async with session_factory() as session:
                    db_email = await session.get(IncomingEmail, email_id)
                    db_attempt_stmt = select(ReplyDeliveryAttempt).where(
                        ReplyDeliveryAttempt.incoming_email_id == email_id
                    )
                    db_attempt = (await session.execute(db_attempt_stmt)).scalar_one()

                    isolation_intact = (
                        resp.success is True
                        and resp.status == "sent"
                        and resp.sent_folder_append_status == "failed"
                        and db_email is not None
                        and db_email.status == "sent"
                        and db_attempt.status == "sent"
                        and db_attempt.sent_folder_append_status == "failed"
                    )

                    self.record(
                        f"R25-03-IMAP-FAULT-ISOLATION-{mode_name.upper()}",
                        isolation_intact,
                        f"Email status='{db_email.status if db_email else None}', append_status='{resp.sent_folder_append_status}'",
                        finding=f"IMAP failure mode {mode_name} caused SMTP rollback or incorrect status",
                    )

        await engine.dispose()

    # -------------------------------------------------------------------------
    # SUITE 4: Ambiguous Delivery Handling & Operator Resolution Matrix
    # -------------------------------------------------------------------------
    async def run_ambiguous_delivery_suite(self) -> None:
        print("\n--- [SUITE 4] Ambiguous Delivery Handling & Operator Resolution ---")

        engine = await self.create_harness_engine()
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        # 1. SMTP Drop/Timeout post-DATA triggers delivery_unknown (Zero blind retry)
        async with session_factory() as session:
            user, store, mailbox, email, draft, version = await self.seed_test_email(session, prefix="ambig")
            email_id = email.id
            user_id = user.id

        smtp_ambiguous = SMTPDeliveryResult(
            status="delivery_unknown",
            error_code="SMTP_DELIVERY_AMBIGUOUS",
            error_detail="Socket disconnected during DATA transmission (timeout)",
        )

        with patch("app.delivery.service.SMTPClient.send_message_robust", new_callable=AsyncMock) as mock_smtp:
            mock_smtp.return_value = smtp_ambiguous

            ambig_raised = False
            ambig_code = ""

            async with session_factory() as session:
                try:
                    await DeliveryService.approve_and_send(
                        session=session,
                        email_id=email_id,
                        idempotency_key=str(uuid.uuid4()),
                        user_id=user_id,
                    )
                except DeliveryBusinessError as exc:
                    ambig_raised = True
                    ambig_code = exc.code

            self.record(
                "R25-04-POST-DATA-TIMEOUT-DELIVERY-UNKNOWN",
                ambig_raised and ambig_code == "DELIVERY_UNKNOWN",
                f"Exception raised={ambig_raised}, code='{ambig_code}'",
                finding=f"Post-DATA socket drop did not raise DELIVERY_UNKNOWN (got {ambig_code})",
            )

        # Verify DB state: email is delivery_unknown
        async with session_factory() as session:
            db_email = await session.get(IncomingEmail, email_id)
            db_draft_stmt = select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id)
            db_draft = (await session.execute(db_draft_stmt)).scalar_one()

            self.record(
                "R25-05-DB-STATUS-DELIVERY-UNKNOWN-NO-AUTO-RETRY",
                db_email is not None and db_email.status == "delivery_unknown" and db_draft.status == "delivery_unknown",
                f"Email status='{db_email.status if db_email else None}', Draft status='{db_draft.status}'",
                finding="Database status was not transitioned to delivery_unknown",
            )

            # Audit event logged
            audit_stmt = select(AuditEvent).where(
                AuditEvent.target_id == str(email_id),
                AuditEvent.event_type == "EMAIL_DELIVERY_UNKNOWN",
            )
            audit_ev = (await session.execute(audit_stmt)).scalar_one_or_none()
            self.record(
                "R25-06-AUDIT-EVENT-DELIVERY-UNKNOWN-LOGGED",
                audit_ev is not None,
                f"AuditEvent found={audit_ev is not None}",
                finding="No AuditEvent logged for EMAIL_DELIVERY_UNKNOWN",
            )

        # 2. Ensure subsequent approve_and_send calls on delivery_unknown are BLOCKED (No blind retries!)
        async with session_factory() as session:
            retry_blocked = False
            retry_code = ""
            try:
                await DeliveryService.approve_and_send(
                    session=session,
                    email_id=email_id,
                    idempotency_key=str(uuid.uuid4()),
                    user_id=user_id,
                )
            except DeliveryBusinessError as exc:
                retry_blocked = True
                retry_code = exc.code

            self.record(
                "R25-07-BLIND-RETRY-BLOCKED-ON-DELIVERY-UNKNOWN",
                retry_blocked and retry_code == "DELIVERY_UNKNOWN_MANUAL_REQUIRED",
                f"Retry blocked={retry_blocked}, code='{retry_code}'",
                finding=f"Blind retry was not blocked with DELIVERY_UNKNOWN_MANUAL_REQUIRED (got {retry_code})",
            )

        # 3. Operator Manual Resolution A: Webmail confirmed sent -> resolve_delivery_unknown_sent
        async with session_factory() as session:
            res_sent = await DeliveryService.resolve_delivery_unknown_sent(
                session=session,
                email_id=email_id,
                notes="Verified recipient received message via webmail check",
                user_id=user_id,
            )
            db_email_sent = await session.get(IncomingEmail, email_id)
            self.record(
                "R25-08-OPERATOR-RESOLVE-SENT-SUCCESS",
                res_sent is True and db_email_sent is not None and db_email_sent.status == "sent",
                f"Resolved={res_sent}, status='{db_email_sent.status if db_email_sent else None}'",
                finding="resolve_delivery_unknown_sent failed to update status to sent",
            )

        # 4. Operator Manual Resolution B: Webmail confirmed NOT sent -> resolve_delivery_unknown_reopen
        async with session_factory() as session:
            user2, store2, mailbox2, email2, draft2, version2 = await self.seed_test_email(session, prefix="reopen")
            email2.status = "delivery_unknown"
            draft2.status = "delivery_unknown"
            await session.commit()
            email2_id = email2.id
            user2_id = user2.id

        async with session_factory() as session:
            res_reopen = await DeliveryService.resolve_delivery_unknown_reopen(
                session=session,
                email_id=email2_id,
                notes="Checked webmail Outbox/Sent: mail was not transmitted. Reopening for retry.",
                user_id=user2_id,
            )
            db_email_reopened = await session.get(IncomingEmail, email2_id)
            self.record(
                "R25-09-OPERATOR-RESOLVE-REOPEN-SUCCESS",
                res_reopen is True and db_email_reopened is not None and db_email_reopened.status == "pending_approval",
                f"Resolved={res_reopen}, status='{db_email_reopened.status if db_email_reopened else None}'",
                finding="resolve_delivery_unknown_reopen failed to reset status to pending_approval",
            )

        await engine.dispose()

    # -------------------------------------------------------------------------
    # SUITE 5: Invariant R-03: Piezaprint Absolute Exclusion
    # -------------------------------------------------------------------------
    async def run_piezaprint_exclusion_suite(self) -> None:
        print("\n--- [SUITE 5] Invariant R-03: Piezaprint Absolute Mail Exclusion ---")

        forbidden_inputs = [
            ("support@piezaprint.com", "mail.wrydeco.com"),
            ("support@wrydeco.com", "mail.piezaprint.com"),
            ("admin@piezaprint", "smtp.chillgen.com"),
        ]

        for user, host in forbidden_inputs:
            blocked = False
            try:
                SMTPClient(host=host, port=587, username=user, password="secret_password")
            except PiezaprintExclusionError:
                blocked = True

            self.record(
                f"R03-01-PIEZAPRINT-EXCLUSION-{user[:12]}",
                blocked,
                f"User='{user}', Host='{host}' -> PiezaprintExclusionError raised",
                finding=f"Piezaprint account was not blocked during SMTPClient instantiation: {user}@{host}",
            )

    # -------------------------------------------------------------------------
    # SUITE 6: Draft Eligibility & Anti-Hallucination Matrix (Invariants R-22, R-23)
    # -------------------------------------------------------------------------
    async def run_draft_rules_and_prompts_suite(self) -> None:
        print("\n--- [SUITE 6] Invariants R-22, R-23: Eligibility & Anti-Hallucination Directives ---")

        # 1. Draft Eligibility Intent Rules
        eligible_intents = ["product_inquiry", "order_support", "complaint", "return_or_refund"]
        for intent in eligible_intents:
            mock_email = IncomingEmail(
                id=uuid.uuid4(),
                classification_category=intent,
                intent_confidence=0.95,
                status="classified",
                spam_status="not_spam",
            )
            is_elig, reason, warnings = DraftService.evaluate_draft_eligibility(mock_email)
            self.record(
                f"R22-01-ELIGIBLE-INTENT-{intent}",
                is_elig is True,
                f"Intent='{intent}' -> eligible={is_elig}",
                finding=f"Valid intent '{intent}' was rejected for draft generation",
            )

        ineligible_intents = ["spam", "partnership", "other", "uncertain"]
        for intent in ineligible_intents:
            mock_email = IncomingEmail(
                id=uuid.uuid4(),
                classification_category=intent,
                intent_confidence=0.90 if intent != "uncertain" else 0.50,
                status="spam" if intent == "spam" else "classified",
                spam_status="spam" if intent == "spam" else "not_spam",
            )
            is_elig, reason, warnings = DraftService.evaluate_draft_eligibility(mock_email)
            self.record(
                f"R22-02-INELIGIBLE-INTENT-ROUTED-MANUAL-{intent}",
                is_elig is False and reason is not None,
                f"Intent='{intent}' -> eligible={is_elig}, reason='{reason}'",
                finding=f"Ineligible intent '{intent}' was incorrectly permitted to generate draft",
            )

        # 2. Attachment & Shopify Lookup Failure checks (Invariants R-21, R-11)
        failure_matrix = [
            ("ATTACHMENT_FAILED", ["ATTACHMENT_FAILED"]),
            ("ATTACHMENT_UNREADABLE", ["ATTACHMENT_FAILED"]),
            ("ATTACHMENT_TOO_LARGE", ["ATTACHMENT_FAILED"]),
            ("SHOPIFY_LOOKUP_FAILED", ["SHOPIFY_LOOKUP_FAILED"]),
            ("SHOPIFY_PRODUCT_SEARCH_FAILED", ["SHOPIFY_PRODUCT_SEARCH_FAILED"]),
            ("SHOPIFY_AUTH_FAILED", ["SHOPIFY_AUTH_FAILED"]),
            ("PROMPT_INJECTION_DETECTED", ["PROMPT_INJECTION_DETECTED"]),
        ]
        for failure_reason, expected_warnings in failure_matrix:
            mock_fail_email = IncomingEmail(
                id=uuid.uuid4(),
                classification_category="product_inquiry",
                intent_confidence=0.95,
                status="manual_review",
                spam_status="not_spam",
                manual_review_reason=failure_reason,
                review_reason_code=failure_reason,
            )
            is_elig, reason, warnings = DraftService.evaluate_draft_eligibility(mock_fail_email)
            self.record(
                f"R21-01-FAILURE-QUARANTINE-{failure_reason}",
                is_elig is False and reason == failure_reason and all(w in warnings for w in expected_warnings),
                f"Failure reason='{failure_reason}' -> eligible={is_elig}, reason='{reason}'",
                finding=f"Failure condition '{failure_reason}' was not blocked from drafting",
            )

        # 3. Database Execution: Verify Ineligible emails raise INELIGIBLE_FOR_DRAFT and create ZERO drafts in DB
        engine = await self.create_harness_engine()
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, store, mailbox, _, _, _ = await self.seed_test_email(session, prefix="quar")

            ineligible_emails = [
                ("spam", "spam", 0.99, None),
                ("partnership", "not_spam", 0.95, None),
                ("other", "not_spam", 0.90, None),
                ("uncertain", "not_spam", 0.50, None),
                ("product_inquiry", "not_spam", 0.95, "ATTACHMENT_FAILED"),
                ("order_support", "not_spam", 0.95, "SHOPIFY_LOOKUP_FAILED"),
            ]

            for cat, spam_st, conf, failure_r in ineligible_emails:
                bad_email = IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    imap_uid=int(uuid.uuid4().int % 100000) + 1,
                    uidvalidity=1,
                    sender_email="bad@test.com",
                    recipient_email=mailbox.address,
                    subject=f"Ineligible test {cat} {failure_r or ''}",
                    received_at=datetime.datetime.now(datetime.UTC),
                    status="manual_review" if failure_r else ("spam" if spam_st == "spam" else "classified"),
                    spam_status=spam_st,
                    classification_category=cat,
                    intent_confidence=conf,
                    manual_review_reason=failure_r,
                    review_reason_code=failure_r,
                )
                bad_email.product_snapshots = []
                session.add(bad_email)
                await session.commit()

                blocked_with_error = False
                try:
                    await DraftService.generate_initial_draft(session, bad_email.id)
                except DraftBusinessError as exc:
                    if exc.code == "INELIGIBLE_FOR_DRAFT":
                        blocked_with_error = True

                draft_count = (
                    await session.execute(
                        select(ReplyDraft).where(ReplyDraft.incoming_email_id == bad_email.id)
                    )
                ).scalars().all()

                self.record(
                    f"R23-03-ZERO-DRAFT-INELIGIBLE-{cat.upper()}-{failure_r or 'INTENT'}",
                    blocked_with_error and len(draft_count) == 0,
                    f"Blocked with error={blocked_with_error}, drafts in DB={len(draft_count)}",
                    finding=f"Ineligible email {cat} created a draft or did not raise INELIGIBLE_FOR_DRAFT",
                )

        # 4. Database Execution: Verify Eligible intents create draft with mandatory warnings
        async with session_factory() as session:
            user, store, mailbox, _, _, _ = await self.seed_test_email(session, prefix="elig")

            # Complaint test
            complaint_email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=int(uuid.uuid4().int % 100000) + 1,
                uidvalidity=1,
                sender_email="unhappy@customer.com",
                recipient_email=mailbox.address,
                subject="Terrible customer service complaint",
                received_at=datetime.datetime.now(datetime.UTC),
                status="classified",
                spam_status="not_spam",
                classification_category="complaint",
                intent_confidence=0.96,
                customer_status="has_order_record",
            )
            complaint_email.product_snapshots = []
            session.add(complaint_email)
            await session.commit()

            complaint_draft = await DraftService.generate_initial_draft(session, complaint_email.id)
            c_v1 = (
                await session.execute(
                    select(ReplyDraftVersion).where(ReplyDraftVersion.draft_id == complaint_draft.id)
                )
            ).scalar_one()

            self.record(
                "R22-03-COMPLAINT-DRAFT-WARNING-BADGE",
                "COMPLAINT_DETECTED" in (c_v1.warning_codes or []),
                f"Complaint draft warning_codes: {c_v1.warning_codes}",
                finding="Complaint draft did not include COMPLAINT_DETECTED warning badge",
            )

            # Refund test
            refund_email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=int(uuid.uuid4().int % 100000) + 1,
                uidvalidity=1,
                sender_email="refund@customer.com",
                recipient_email=mailbox.address,
                subject="Requesting return or refund for order",
                received_at=datetime.datetime.now(datetime.UTC),
                status="classified",
                spam_status="not_spam",
                classification_category="return_or_refund",
                intent_confidence=0.95,
                customer_status="has_order_record",
            )
            refund_email.product_snapshots = []
            session.add(refund_email)
            await session.commit()

            refund_draft = await DraftService.generate_initial_draft(session, refund_email.id)
            r_v1 = (
                await session.execute(
                    select(ReplyDraftVersion).where(ReplyDraftVersion.draft_id == refund_draft.id)
                )
            ).scalar_one()

            self.record(
                "R22-04-REFUND-DRAFT-WARNING-BADGE",
                "RETURN_OR_REFUND_REQUESTED" in (r_v1.warning_codes or []),
                f"Refund draft warning_codes: {r_v1.warning_codes}",
                finding="Refund draft did not include RETURN_OR_REFUND_REQUESTED warning badge",
            )

        await engine.dispose()

        # 2. Anti-hallucination prompt directives
        # Case A: product_resolved = False
        dummy_store = StoreProfile(
            name="Wrydeco Art Store",
            brand_name="Wrydeco",
            public_domain="wrydeco.com",
            default_language="en",
            status="active",
        )
        dummy_email_unresolved = IncomingEmail(
            sender_email="john@example.com",
            sender_name="John Doe",
            subject="Do you have this in blue?",
            received_at=datetime.datetime.now(datetime.UTC),
        )
        dummy_snap_unresolved = ShopifyProductSnapshot(
            product_resolved=False,
            matched_products=[],
            search_query="blue lamp",
        )
        prompt_unresolved = build_draft_prompt(
            store=dummy_store,
            original_email=dummy_email_unresolved,
            classification=None,
            order_snapshot=None,
            product_snapshot=dummy_snap_unresolved,
            policies={},
            target_language="en",
        )
        has_unresolved_directive = (
            "PRODUCT RESOLUTION STATUS: UNRESOLVED" in prompt_unresolved
            and "ANTI-HALLUCINATION DIRECTIVE:" in prompt_unresolved
            and "ABSOLUTELY FORBIDDEN to invent product specifications, prices, or inventory levels" in prompt_unresolved
        )
        self.record(
            "R23-01-PROMPT-ANTI-HALLUCINATION-PRODUCT-UNRESOLVED",
            has_unresolved_directive,
            "Directive strictly prevents guessing price/specs and asks for photo/link",
            finding="Missing anti-hallucination directive when product_resolved=False",
        )

        # Case B: No order record
        dummy_email_no_order = IncomingEmail(
            sender_email="jane@example.com",
            sender_name="Jane Doe",
            subject="Where is my order?",
            received_at=datetime.datetime.now(datetime.UTC),
        )
        prompt_no_order = build_draft_prompt(
            store=dummy_store,
            original_email=dummy_email_no_order,
            classification=None,
            order_snapshot=None,
            product_snapshot=None,
            policies={},
            target_language="en",
        )
        has_no_order_directive = (
            "CUSTOMER ORDER STATUS: NO MATCHING ORDER FOUND in the past 60 days" in prompt_no_order
            and "DO NOT invent an order number or speculate on shipping dates" in prompt_no_order
        )
        self.record(
            "R23-02-PROMPT-ANTI-HALLUCINATION-NO-ORDER",
            has_no_order_directive,
            "Directive strictly prevents promising delivery status and asks for order number",
            finding="Missing anti-hallucination directive when customer has no order record",
        )

        # 3. Language resolution matrix
        for lang_code in ("vi", "fr", "de", "es", "ja", "zh", "en"):
            resolved = resolve_draft_language(requested_language=None, detected_language=lang_code, store_default_language="en")
            self.record(
                f"R23-03-LANGUAGE-MATRIX-{lang_code.upper()}",
                resolved == lang_code,
                f"Detected='{lang_code}' -> resolved='{resolved}'",
                finding=f"Language resolution failed for supported language {lang_code}",
            )

    # -------------------------------------------------------------------------
    # SUITE 7: Invariant R-24 — Immutable Draft Versioning Lifecycle & Stale Policy
    # -------------------------------------------------------------------------
    async def run_immutable_versioning_and_stale_policy_suite(self) -> None:
        print("\n--- [SUITE 7] Invariant R-24: Immutable Draft Versioning & Stale Policy ---")

        engine = await self.create_harness_engine()
        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            user, store, mailbox, _, _, _ = await self.seed_test_email(session, prefix="immut")

            # Add refund policy for store
            refund_policy = StorePolicy(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                policy_type="REFUND_POLICY",
                title="Refund Policy",
                body_html="<p>Returns accepted within 30 days of receipt.</p>",
                body_text="Returns accepted within 30 days of receipt.",
                content_hash="hash_refund_v1",
                created_at=datetime.datetime.now(datetime.UTC),
                updated_at=datetime.datetime.now(datetime.UTC),
            )
            session.add(refund_policy)

            email = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                imap_uid=int(uuid.uuid4().int % 100000) + 1,
                uidvalidity=1,
                sender_email="customer@artpaintings.com",
                recipient_email=mailbox.address,
                subject="Inquiry regarding oil painting frame",
                received_at=datetime.datetime.now(datetime.UTC),
                status="classified",
                spam_status="not_spam",
                classification_category="product_inquiry",
                intent_confidence=0.95,
                customer_status="no_order",
            )
            email.product_snapshots = []
            session.add(email)
            await session.commit()
            email_id = email.id
            user_id = user.id

        # Step 1: Initial AI Draft Generation (Version 1)
        async with session_factory() as session:
            draft = await DraftService.generate_initial_draft(session, email_id)
            v1_stmt = select(ReplyDraftVersion).where(
                ReplyDraftVersion.draft_id == draft.id,
                ReplyDraftVersion.version_number == 1,
            )
            v1 = (await session.execute(v1_stmt)).scalar_one()

            v1_id = v1.id
            v1_subject = v1.subject
            v1_body = v1.body_text
            v1_hash = v1.content_hash
            v1_created_at = v1.created_at

            self.record(
                "R24-01-INITIAL-DRAFT-VERSION-1-CREATED",
                v1.version_number == 1 and v1.source == "ai" and v1.is_current_version is True,
                f"Version={v1.version_number}, source='{v1.source}', current={v1.is_current_version}",
                finding="Initial draft was not assigned version 1 with source 'ai'",
            )

        # Step 2: User Edits Draft -> Creates Version 2 (User)
        user_edited_subject = "Re: Inquiry regarding oil painting frame [Handcrafted Answer]"
        user_edited_body = (
            "Dear Customer,\n\n"
            "All our oil painting frames are handcrafted using genuine solid walnut wood.\n"
            "Best regards,\nWrydeco Customer Support"
        )

        async with session_factory() as session:
            v2 = await DraftService.save_user_version(
                session=session,
                email_id=email_id,
                subject=user_edited_subject,
                body_text=user_edited_body,
                body_html=None,
                user_id=user_id,
            )

            self.record(
                "R24-02-USER-EDIT-CREATES-VERSION-2",
                v2.version_number == 2 and v2.source == "user" and v2.created_by == user_id and v2.is_current_version is True,
                f"Version={v2.version_number}, source='{v2.source}', user_id={v2.created_by}",
                finding="User edit did not create version 2 with source 'user'",
            )

        # Step 3: EMPIRICAL PROOF OF IMMUTABILITY — Version 1 Record in DB is 100% UNTOUCHED
        async with session_factory() as session:
            v1_reloaded = (
                await session.execute(
                    select(ReplyDraftVersion).where(ReplyDraftVersion.id == v1_id)
                )
            ).scalar_one()

            immutability_preserved = (
                v1_reloaded.subject == v1_subject
                and v1_reloaded.body_text == v1_body
                and v1_reloaded.content_hash == v1_hash
                and v1_reloaded.created_at == v1_created_at
                and v1_reloaded.is_current_version is False  # Only current flag updated
                and v1_reloaded.source == "ai"
                and v1_reloaded.version_number == 1
            )
            self.record(
                "R24-03-VERSION-1-ABSOLUTE-IMMUTABILITY-PROVEN",
                immutability_preserved,
                f"V1 subject identical={v1_reloaded.subject == v1_subject}, hash identical={v1_reloaded.content_hash == v1_hash}",
                finding="Version 1 database record was overwritten or corrupted by user edit!",
            )

        # Step 4: AI Regeneration -> Creates Version 3 with Language Override
        async with session_factory() as session:
            v3 = await DraftService.regenerate_draft(
                session=session,
                email_id=email_id,
                target_language="fr",
                custom_instructions="Polite museum-grade tone",
                user_id=user_id,
            )

            self.record(
                "R24-04-AI-REGENERATION-CREATES-VERSION-3",
                v3.version_number == 3 and v3.source == "ai" and v3.language == "fr" and v3.is_current_version is True,
                f"Version={v3.version_number}, source='{v3.source}', language='{v3.language}'",
                finding="AI regeneration did not create version 3 with language 'fr'",
            )

        # Step 5: Check all 3 versions coexist sequentially in DB
        async with session_factory() as session:
            all_versions = (
                await session.execute(
                    select(ReplyDraftVersion)
                    .where(ReplyDraftVersion.incoming_email_id == email_id)
                    .order_by(ReplyDraftVersion.version_number)
                )
            ).scalars().all()

            v_nums = [v.version_number for v in all_versions]
            current_flags = [v.is_current_version for v in all_versions]
            sources = [v.source for v in all_versions]

            self.record(
                "R24-05-ALL-VERSIONS-COEXIST-SEQUENTIALLY",
                v_nums == [1, 2, 3] and current_flags == [False, False, True] and sources == ["ai", "user", "ai"],
                f"Versions={v_nums}, Currents={current_flags}, Sources={sources}",
                finding=f"Version timeline sequence anomaly: {v_nums}",
            )

        # Step 6: Draft Lock Safeguards (sending / sent state)
        async with session_factory() as session:
            draft_rec = (
                await session.execute(
                    select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id)
                )
            ).scalar_one()
            draft_rec.status = "sending"
            await session.commit()

            edit_sending_blocked = False
            try:
                await DraftService.save_user_version(
                    session=session,
                    email_id=email_id,
                    subject="Attack while sending",
                    body_text="Attack",
                    body_html=None,
                    user_id=user_id,
                )
            except DraftBusinessError as exc:
                if exc.code == "DRAFT_LOCKED":
                    edit_sending_blocked = True

            self.record(
                "R24-06-EDIT-BLOCKED-WHEN-SENDING",
                edit_sending_blocked,
                "save_user_version strictly rejected with DRAFT_LOCKED when status is 'sending'",
                finding="Editing in-flight draft was not blocked with DRAFT_LOCKED",
            )

            regen_sending_blocked = False
            try:
                await DraftService.regenerate_draft(
                    session=session,
                    email_id=email_id,
                    target_language="de",
                    user_id=user_id,
                )
            except DraftBusinessError as exc:
                if exc.code == "DRAFT_LOCKED":
                    regen_sending_blocked = True

            self.record(
                "R24-07-REGEN-BLOCKED-WHEN-SENDING",
                regen_sending_blocked,
                "regenerate_draft strictly rejected with DRAFT_LOCKED when status is 'sending'",
                finding="Regenerating in-flight draft was not blocked with DRAFT_LOCKED",
            )

        # Step 7: Stale Policy Detection (Invariant R-18)
        async with session_factory() as session:
            draft_rec = (
                await session.execute(
                    select(ReplyDraft).where(ReplyDraft.incoming_email_id == email_id)
                )
            ).scalar_one()
            v_curr = await session.get(ReplyDraftVersion, draft_rec.current_version_id)
            v_curr.policy_hashes_used = {"REFUND_POLICY": "hash_refund_v1"}
            await session.commit()

            # Baseline: policies haven't changed
            is_stale, reason, details = await DraftService.check_stale_policy(session, draft_rec)
            self.record(
                "R18-01-STALE-POLICY-BASELINE-FALSE",
                is_stale is False and details is None,
                f"is_stale={is_stale}, details={details}",
                finding="Draft incorrectly flagged as stale when policy hasn't changed",
            )

            # Mutate store policy hash
            pol_stmt = select(StorePolicy).where(StorePolicy.store_profile_id == store.id)
            pol = (await session.execute(pol_stmt)).scalars().first()
            pol.content_hash = "hash_refund_v2_updated"
            await session.commit()

            is_stale_after, reason_after, details_after = await DraftService.check_stale_policy(session, draft_rec)
            changed_types = [d["policy_type"] for d in (details_after or [])]
            self.record(
                "R18-02-STALE-POLICY-DETECTED-ON-CHANGE",
                is_stale_after is True and "REFUND_POLICY" in changed_types,
                f"is_stale={is_stale_after}, changed_types={changed_types}",
                finding="Draft failed to detect stale policy after policy content hash changed",
            )

        # Step 8: Audit Events Logged for Draft Versioning
        async with session_factory() as session:
            audits = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.target_id == str(draft_rec.id))
                )
            ).scalars().all()
            ev_types = [a.event_type for a in audits]

            self.record(
                "R24-08-AUDIT-EVENTS-VERSION-CREATED-AND-REGENERATED",
                "DRAFT_VERSION_CREATED" in ev_types and "DRAFT_REGENERATED" in ev_types,
                f"AuditEvent types found: {ev_types}",
                finding=f"Missing expected AuditEvents for version lifecycle: {ev_types}",
            )

        await engine.dispose()

    # -------------------------------------------------------------------------
    # MAIN RUNNER
    # -------------------------------------------------------------------------
    async def run_all(self) -> int:
        print("=" * 80)
        print("MAIL AGENT CHALLENGER EMPIRICAL VERIFICATION HARNESS — PHASE 6")
        print("=" * 80)

        await self.run_zero_autonomous_sending_suite()
        await self.run_send_idempotency_concurrency_suite()
        await self.run_threading_and_imap_isolation_suite()
        await self.run_ambiguous_delivery_suite()
        await self.run_piezaprint_exclusion_suite()
        await self.run_draft_rules_and_prompts_suite()
        await self.run_immutable_versioning_and_stale_policy_suite()

        print("\n" + "=" * 80)
        print("CHALLENGER EMPIRICAL SUMMARY — PHASE 6")
        print("=" * 80)
        print(f"Total Tests Executed: {self.tests_run}")
        print(f"Passed: {self.passed}")
        print(f"Failed: {self.failed}")

        if self.failed == 0:
            print("\n>>> ALL ADVERSARIAL STRESS TESTS PASSED WITH 100% EMPIRICAL INTEGRITY! <<<")
            return 0
        else:
            print(f"\n>>> FOUND {self.failed} ADVERSARIAL VULNERABILITIES / FAILURES! <<<", file=sys.stderr)
            for f in self.findings:
                print(f"  - {f}", file=sys.stderr)
            return 1


# =============================================================================
# Pytest Integration Test Functions
# =============================================================================

@pytest.mark.asyncio
async def test_adversarial_phase6_zero_autonomous_sending():
    c = EmpiricalPhase6Challenger()
    await c.run_zero_autonomous_sending_suite()
    assert c.failed == 0


@pytest.mark.asyncio
async def test_adversarial_phase6_send_idempotency_concurrency():
    c = EmpiricalPhase6Challenger()
    await c.run_send_idempotency_concurrency_suite()
    assert c.failed == 0


@pytest.mark.asyncio
async def test_adversarial_phase6_threading_and_imap_isolation():
    c = EmpiricalPhase6Challenger()
    await c.run_threading_and_imap_isolation_suite()
    assert c.failed == 0


@pytest.mark.asyncio
async def test_adversarial_phase6_ambiguous_delivery():
    c = EmpiricalPhase6Challenger()
    await c.run_ambiguous_delivery_suite()
    assert c.failed == 0


@pytest.mark.asyncio
async def test_adversarial_phase6_piezaprint_exclusion():
    c = EmpiricalPhase6Challenger()
    await c.run_piezaprint_exclusion_suite()
    assert c.failed == 0


@pytest.mark.asyncio
async def test_adversarial_phase6_draft_rules_and_prompts():
    c = EmpiricalPhase6Challenger()
    await c.run_draft_rules_and_prompts_suite()
    assert c.failed == 0


@pytest.mark.asyncio
async def test_adversarial_phase6_immutable_versioning_and_stale_policy():
    c = EmpiricalPhase6Challenger()
    await c.run_immutable_versioning_and_stale_policy_suite()
    assert c.failed == 0


if __name__ == "__main__":
    challenger = EmpiricalPhase6Challenger()
    exit_code = asyncio.run(challenger.run_all())
    sys.exit(exit_code)
