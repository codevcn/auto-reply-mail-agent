"""Challenger Empirical Adversarial Test Harness for Phase 5.

Adversarial Stress Testing covering:
1. Invariant R-02 (Fail-closed SOCKS5 Proxy):
   - Dead/unreachable proxy, timeout, missing config, direct fallback rejection.
   - SocketTrafficSpy verifies ZERO traffic leak to target destination.
2. Invariant R-09 (Email Normalization, 60-Day Filter, Test Order Exclusion):
   - Case variations, trimming, preservation of dots and '+' sub-addressing.
   - Strict 60-day boundary tests (59d 23h 59m vs 60d 0s vs 60d 1s).
   - Absolute exclusion of test orders (test=true).
3. Invariant R-10 (Cancelled/Refunded Order Relationship):
   - Cancelled orders preserve has_order_record=True and activate has_cancelled_order.
   - Refunded orders preserve has_order_record=True and activate has_refunded_order.
   - Independent 5 status flags matrix (paid, active, cancelled, refunded, fulfilled).
4. Invariant R-11 (Strict Distinction: lookup_unavailable vs no_order):
   - Proxy/transient/rate-limit/timeout errors mark lookup_unavailable, NEVER no_order.
   - Only 200 OK with zero matching orders produces no_order.
5. Invariant R-03 (Piezaprint Absolute Multi-Layer Exclusion):
   - Domain validation, casing attacks, subdomains, IMAP/SMTP client instantiation.
6. Error Retry Schedule (Section 14.1):
   - Exponential delays [60s, 300s, 900s, 1800s, 3600s] on attempts 1-4.
   - Retry exhaustion at attempt 5 transitions to manual_review with SHOPIFY_LOOKUP_FAILED.
   - Terminal 401 (ShopifyAuthError) and 403 (ShopifyPermissionError) halt retries immediately.
7. Invariant R-16, R-17, R-18 (Product Search, 6 Policies Sync, Stale Draft Check):
   - Zero Catalog Mirroring verification.
   - Live product search zero-result warning (PRODUCT_NOT_RESOLVED).
   - 6 policy types sync, sanitization, 64-hex SHA-256 hashing.
   - Stale draft detection when policy hash changes.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import socket
import sys
import threading
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

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models.email import EmailJob, IncomingEmail
from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
from app.db.models.store import Mailbox, StorePolicy, StoreProfile, ShopifyConnection, ProxyProfile
from app.mail.exceptions import PiezaprintExclusionError
from app.mail.imap_client import IMAPClient
from app.mail.smtp_client import SMTPClient
from app.proxy.client import ResolvedProxyConfig
from app.proxy.exceptions import (
    ProxyConfigurationError,
    ProxyConnectionError,
)
from app.proxy.transport import create_proxy_enforced_client
from app.queue.service import (
    MAX_SHOPIFY_LOOKUP_ATTEMPTS,
    SHOPIFY_LOOKUP_RETRY_DELAYS_SECONDS,
    TransactionalQueueService,
)
from app.shopify.cleaner import (
    compute_content_hash,
    normalize_policy_text,
    sanitize_policy_html,
)
from app.shopify.client import ProxyEnforcedShopifyClient
from app.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyPermissionError,
    ShopifyProxyError,
    ShopifyRateLimitError,
    ShopifyTransientError,
)
from app.shopify.schemas import PolicyDict, StorePolicyItem
from app.shopify.service import ShopifyService, _memory_policy_cache
from app.shopify.token import ShopifyTokenManager
from app.store.services import EXCLUDED_STORE_DOMAINS, StoreWizardService


class SocketTrafficSpy:
    """A raw TCP socket server that listens on localhost and records any inbound connection/traffic."""

    def __init__(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.port: int = self.server_socket.getsockname()[1]
        self.server_socket.listen(5)
        self.connection_count = 0
        self.bytes_received = 0
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.server_socket.settimeout(0.2)
        while self._running:
            try:
                conn, _ = self.server_socket.accept()
                self.connection_count += 1
                conn.settimeout(0.5)
                try:
                    data = conn.recv(4096)
                    self.bytes_received += len(data)
                except Exception:
                    pass
                finally:
                    conn.close()
            except TimeoutError:
                continue
            except OSError:
                break

    def close(self) -> None:
        self._running = False
        try:
            self.server_socket.close()
        except Exception:
            pass


class EmpiricalPhase5Challenger:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.tests_run = 0
        self.findings: list[str] = []
        self.results: list[dict[str, Any]] = []

    def record(self, test_id: str, success: bool, details: str = "", finding: str | None = None) -> None:
        self.tests_run += 1
        if success:
            self.passed += 1
            print(f"  [PASS] {test_id} | {details}")
        else:
            self.failed += 1
            print(f"  [FAIL] {test_id} | {details}", file=sys.stderr)
            if finding:
                self.findings.append(f"[{test_id}] {finding}")
        self.results.append({"id": test_id, "success": success, "details": details, "finding": finding})

    async def create_isolated_db(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        return engine, session_factory

    # =========================================================================
    # SUITE 1: INVARIANT R-02 (FAIL-CLOSED SOCKS5 PROXY & ZERO DIRECT LEAK)
    # =========================================================================
    async def run_fail_closed_proxy_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 1: INVARIANT R-02 (FAIL-CLOSED SOCKS5 PROXY & ZERO DIRECT LEAK)")
        print("=" * 80)

        # Find an unused port to simulate dead proxy
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        temp_sock.bind(("127.0.0.1", 0))
        dead_port = temp_sock.getsockname()[1]
        temp_sock.close()

        spy = SocketTrafficSpy()
        try:
            # 1.1 Dead proxy GraphQL call -> must raise ShopifyProxyError and ZERO bytes to target spy
            dead_proxy_config = ResolvedProxyConfig(
                host="127.0.0.1",
                port=dead_port,
                protocol="socks5",
                connect_timeout=1,
            )
            token_mgr = ShopifyTokenManager(
                shop_domain=f"127.0.0.1:{spy.port}",
                client_id="dummy_id",
                client_secret="dummy_secret",
                proxy_config=dead_proxy_config,
                initial_token="shpat_test_token",
                initial_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
            )
            client = ProxyEnforcedShopifyClient(
                shop_domain=f"127.0.0.1:{spy.port}",
                token_manager=token_mgr,
                proxy_config=dead_proxy_config,
            )

            threw_proxy_error = False
            try:
                await client.execute_graphql("{ shop { id } }")
            except ShopifyProxyError:
                threw_proxy_error = True
            except Exception as e:
                threw_proxy_error = False
                print(f"    Unexpected exception: {type(e)}: {e}")

            leak_detected = spy.connection_count > 0 or spy.bytes_received > 0
            self.record(
                "R02-01-DEAD-PROXY-GRAPHQL",
                threw_proxy_error and not leak_detected,
                f"Threw ShopifyProxyError={threw_proxy_error}, Target spy connections={spy.connection_count}, bytes={spy.bytes_received}",
                finding="Dead proxy did not fail closed or leaked traffic to target" if (not threw_proxy_error or leak_detected) else None,
            )

            # 1.2 Dead proxy REST call -> must raise ShopifyProxyError and ZERO bytes to target spy
            spy.connection_count = 0
            spy.bytes_received = 0
            threw_proxy_error_rest = False
            try:
                await client.execute_rest("orders.json")
            except ShopifyProxyError:
                threw_proxy_error_rest = True
            except Exception as e:
                threw_proxy_error_rest = False
                print(f"    Unexpected exception: {type(e)}: {e}")

            leak_detected_rest = spy.connection_count > 0 or spy.bytes_received > 0
            self.record(
                "R02-02-DEAD-PROXY-REST",
                threw_proxy_error_rest and not leak_detected_rest,
                f"Threw ShopifyProxyError={threw_proxy_error_rest}, Target spy connections={spy.connection_count}",
                finding="Dead proxy REST did not fail closed or leaked traffic" if (not threw_proxy_error_rest or leak_detected_rest) else None,
            )

            # 1.3 Missing proxy config (None) -> must raise ProxyConfigurationError immediately
            threw_config_err = False
            try:
                create_proxy_enforced_client(None)
            except ProxyConfigurationError:
                threw_config_err = True
            except Exception:
                pass

            self.record(
                "R02-03-MISSING-PROXY-CONFIG",
                threw_config_err,
                f"create_proxy_enforced_client(None) raised ProxyConfigurationError={threw_config_err}",
                finding="Missing proxy config did not raise ProxyConfigurationError",
            )

            # 1.4 Direct fallback attempt rejected
            threw_fallback_err = False
            try:
                create_proxy_enforced_client(dead_proxy_config, allow_direct_fallback=True)
            except ProxyConfigurationError:
                threw_fallback_err = True
            except Exception:
                pass

            self.record(
                "R02-04-ALLOW-DIRECT-FALLBACK-PROHIBITED",
                threw_fallback_err,
                f"allow_direct_fallback=True rejected={threw_fallback_err}",
                finding="allow_direct_fallback=True was not rejected by transport factory",
            )

            # 1.5 Remote DNS resolution scheme socks5h:// enforced
            proxy_url = dead_proxy_config.get_proxy_url()
            remote_dns_enforced = proxy_url.startswith("socks5h://")
            self.record(
                "R02-05-REMOTE-DNS-SOCKS5H-ENFORCED",
                remote_dns_enforced,
                f"Proxy URL scheme: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}",
                finding="Proxy URL does not use socks5h:// to enforce remote DNS resolution",
            )

        finally:
            spy.close()

    # =========================================================================
    # SUITE 2: INVARIANT R-09 (EMAIL NORMALIZATION, 60-DAY WINDOW, TEST ORDERS)
    # =========================================================================
    async def run_email_norm_and_60day_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 2: INVARIANT R-09 (EMAIL NORMALIZATION & 60-DAY & TEST ORDER FILTER)")
        print("=" * 80)

        # 2.1 Email Normalization
        norm_cases = [
            ("alice@test.com", "alice@test.com"),
            ("ALICE@TEST.COM", "alice@test.com"),
            ("  alice@test.com  ", "alice@test.com"),
            ("\tAlice@Test.COM\n", "alice@test.com"),
            ("ALICE.SMITH@EXAMPLE.COM", "alice.smith@example.com"),
            ("Bob+Tag@Shop.Co.Uk", "bob+tag@shop.co.uk"),
        ]
        all_norm_ok = True
        for raw, expected in norm_cases:
            res = ShopifyService.normalize_email(raw)
            if res != expected:
                all_norm_ok = False
                print(f"    Failed: {raw!r} -> {res!r} != {expected!r}")

        self.record(
            "R09-01-EMAIL-NORMALIZATION-BASIC",
            all_norm_ok,
            f"Tested {len(norm_cases)} variations (case, whitespace trimming)",
            finding="Email normalization failed for case or whitespace variations",
        )

        # 2.2 Dots and Sub-addressing preservation (must NOT strip dots or +tags)
        dot_email = "a.l.i.c.e@test.com"
        tag_email = "alice+promo10@test.com"
        norm_dot = ShopifyService.normalize_email(dot_email)
        norm_tag = ShopifyService.normalize_email(tag_email)

        preserves_dots = norm_dot == "a.l.i.c.e@test.com" and norm_dot != "alice@test.com"
        preserves_tag = norm_tag == "alice+promo10@test.com" and norm_tag != "alice@test.com"
        self.record(
            "R09-02-EMAIL-PRESERVE-DOTS-AND-TAGS",
            preserves_dots and preserves_tag,
            f"Preserves dots: {preserves_dots}, Preserves tags: {preserves_tag}",
            finding="Email normalization incorrectly stripped dots or +tags",
        )

        # 2.3 Strict 60-Day Boundary Matrix
        t0 = datetime.datetime(2026, 10, 3, 12, 0, 0, tzinfo=datetime.UTC)

        order_within_59d = {
            "id": "ord_59d",
            "created_at": (t0 - datetime.timedelta(days=59, hours=23, minutes=59)).isoformat(),
            "financial_status": "paid",
            "test": False,
        }
        order_exact_60d = {
            "id": "ord_exact_60d",
            "created_at": (t0 - datetime.timedelta(days=60)).isoformat(),
            "financial_status": "paid",
            "test": False,
        }
        order_outside_60d_1s = {
            "id": "ord_outside_1s",
            "created_at": (t0 - datetime.timedelta(days=60, seconds=1)).isoformat(),
            "financial_status": "paid",
            "test": False,
        }
        order_outside_90d = {
            "id": "ord_old_90d",
            "created_at": (t0 - datetime.timedelta(days=90)).isoformat(),
            "financial_status": "paid",
            "test": False,
        }

        flags_59d = ShopifyService.classify_order_flags([order_within_59d], window_days=60, reference_time=t0)
        flags_exact = ShopifyService.classify_order_flags([order_exact_60d], window_days=60, reference_time=t0)
        flags_outside = ShopifyService.classify_order_flags([order_outside_60d_1s], window_days=60, reference_time=t0)
        flags_90d = ShopifyService.classify_order_flags([order_outside_90d], window_days=60, reference_time=t0)

        b1 = flags_59d.has_order_record is True and flags_59d.has_recent_order is True and flags_59d.recent_orders_count == 1
        b2 = flags_exact.has_order_record is True and flags_exact.has_recent_order is True and flags_exact.recent_orders_count == 1
        b3 = flags_outside.has_order_record is True and flags_outside.has_recent_order is False and flags_outside.recent_orders_count == 0
        b4 = flags_90d.has_order_record is True and flags_90d.has_recent_order is False and flags_90d.recent_orders_count == 0

        boundary_pass = b1 and b2 and b3 and b4
        self.record(
            "R09-03-60DAY-BOUNDARY-EXACTNESS",
            boundary_pass,
            f"59d23h59m(in)={b1}, exact_60d(in)={b2}, 60d+1s(out)={b3}, 90d(out)={b4}",
            finding="60-day boundary calculation incorrect at exact second cutoffs",
        )

        # 2.4 Test Order Exclusion (test=true)
        test_only_orders = [
            {"id": "t1", "created_at": (t0 - datetime.timedelta(days=2)).isoformat(), "financial_status": "paid", "test": True},
            {"id": "t2", "created_at": (t0 - datetime.timedelta(days=5)).isoformat(), "financial_status": "paid", "test": True},
        ]
        flags_test_only = ShopifyService.classify_order_flags(test_only_orders, window_days=60, reference_time=t0)
        test_only_clean = (
            flags_test_only.has_order_record is False
            and flags_test_only.has_recent_order is False
            and flags_test_only.recent_orders_count == 0
        )
        self.record(
            "R09-04-TEST-ORDERS-PURE-EXCLUSION",
            test_only_clean,
            f"Test-only orders: has_order_record={flags_test_only.has_order_record}, count={flags_test_only.recent_orders_count}",
            finding="Test orders (test=True) were not excluded from has_order_record",
        )

        # Mixed real and test orders
        mixed_orders = [
            {"id": "real_1", "created_at": (t0 - datetime.timedelta(days=15)).isoformat(), "financial_status": "paid", "test": False},
            {"id": "test_1", "created_at": (t0 - datetime.timedelta(days=1)).isoformat(), "financial_status": "paid", "test": True},
            {"id": "test_2", "created_at": (t0 - datetime.timedelta(days=2)).isoformat(), "financial_status": "paid", "test": True},
        ]
        flags_mixed = ShopifyService.classify_order_flags(mixed_orders, window_days=60, reference_time=t0)
        mixed_clean = (
            flags_mixed.has_order_record is True
            and flags_mixed.has_recent_order is True
            and flags_mixed.recent_orders_count == 1
        )
        self.record(
            "R09-05-TEST-ORDERS-MIXED-ISOLATION",
            mixed_clean,
            f"Mixed (1 real + 2 test): count={flags_mixed.recent_orders_count} (expected 1)",
            finding="Test orders contaminated recent_orders_count in mixed dataset",
        )

    # =========================================================================
    # SUITE 3: INVARIANT R-10 (CANCELLED / REFUNDED ORDER RELATIONSHIP)
    # =========================================================================
    async def run_cancelled_refunded_matrix_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 3: INVARIANT R-10 (CANCELLED/REFUNDED RELATIONSHIP & 5 FLAGS)")
        print("=" * 80)

        t0 = datetime.datetime.now(datetime.UTC)

        # 3.1 Cancelled Order Alone
        cancelled_order = [
            {
                "id": "ord_c1",
                "created_at": (t0 - datetime.timedelta(days=10)).isoformat(),
                "financial_status": "voided",
                "cancelled_at": (t0 - datetime.timedelta(days=9)).isoformat(),
                "test": False,
            }
        ]
        flags_c = ShopifyService.classify_order_flags(cancelled_order, window_days=60, reference_time=t0)
        c_ok = (
            flags_c.has_order_record is True
            and flags_c.has_recent_order is True
            and flags_c.has_cancelled_order is True
            and flags_c.has_active_order is False
            and flags_c.has_paid_order is False
        )
        self.record(
            "R10-01-CANCELLED-ORDER-ALONE",
            c_ok,
            f"has_order_record={flags_c.has_order_record}, has_cancelled={flags_c.has_cancelled_order}, has_active={flags_c.has_active_order}",
            finding="Cancelled order failed to preserve customer record or set flags correctly",
        )

        # 3.2 Refunded Order Alone
        refunded_order = [
            {
                "id": "ord_r1",
                "created_at": (t0 - datetime.timedelta(days=20)).isoformat(),
                "financial_status": "refunded",
                "test": False,
            }
        ]
        flags_r = ShopifyService.classify_order_flags(refunded_order, window_days=60, reference_time=t0)
        r_ok = (
            flags_r.has_order_record is True
            and flags_r.has_recent_order is True
            and flags_r.has_refunded_order is True
            and flags_r.has_active_order is False
        )
        self.record(
            "R10-02-REFUNDED-ORDER-ALONE",
            r_ok,
            f"has_order_record={flags_r.has_order_record}, has_refunded={flags_r.has_refunded_order}, has_active={flags_r.has_active_order}",
            finding="Refunded order failed to preserve customer record or set flags correctly",
        )

        # 3.3 Partially Refunded Order (paid + refunded both true)
        partial_order = [
            {
                "id": "ord_pr1",
                "created_at": (t0 - datetime.timedelta(days=12)).isoformat(),
                "financial_status": "partially_refunded",
                "fulfillment_status": "unfulfilled",
                "test": False,
            }
        ]
        flags_pr = ShopifyService.classify_order_flags(partial_order, window_days=60, reference_time=t0)
        pr_ok = (
            flags_pr.has_order_record is True
            and flags_pr.has_paid_order is True
            and flags_pr.has_refunded_order is True
            and flags_pr.has_active_order is True
        )
        self.record(
            "R10-03-PARTIALLY-REFUNDED-ORDER",
            pr_ok,
            f"has_paid={flags_pr.has_paid_order}, has_refunded={flags_pr.has_refunded_order}, has_active={flags_pr.has_active_order}",
            finding="Partially refunded order flags incorrect",
        )

        # 3.4 Fulfilled Order (fulfilled=True, active=False)
        fulfilled_order = [
            {
                "id": "ord_f1",
                "created_at": (t0 - datetime.timedelta(days=30)).isoformat(),
                "financial_status": "paid",
                "fulfillment_status": "fulfilled",
                "test": False,
            }
        ]
        flags_f = ShopifyService.classify_order_flags(fulfilled_order, window_days=60, reference_time=t0)
        f_ok = (
            flags_f.has_order_record is True
            and flags_f.has_fulfilled_order is True
            and flags_f.has_active_order is False
            and flags_f.has_paid_order is True
        )
        self.record(
            "R10-04-FULFILLED-ORDER",
            f_ok,
            f"has_fulfilled={flags_f.has_fulfilled_order}, has_active={flags_f.has_active_order}",
            finding="Fulfilled order was incorrectly marked as active_order",
        )

        # 3.5 Multi-order Matrix: Cancelled past order + Active recent order
        multi_orders = [
            {
                "id": "ord_act",
                "created_at": (t0 - datetime.timedelta(days=5)).isoformat(),
                "financial_status": "paid",
                "fulfillment_status": "unfulfilled",
                "test": False,
            },
            {
                "id": "ord_canc",
                "created_at": (t0 - datetime.timedelta(days=45)).isoformat(),
                "financial_status": "voided",
                "cancelled_at": (t0 - datetime.timedelta(days=44)).isoformat(),
                "test": False,
            },
        ]
        flags_multi = ShopifyService.classify_order_flags(multi_orders, window_days=60, reference_time=t0)
        multi_ok = (
            flags_multi.has_order_record is True
            and flags_multi.has_recent_order is True
            and flags_multi.recent_orders_count == 2
            and flags_multi.has_active_order is True
            and flags_multi.has_cancelled_order is True
            and flags_multi.has_paid_order is True
        )
        self.record(
            "R10-05-MULTI-ORDER-INDEPENDENT-FLAGS",
            multi_ok,
            f"has_active={flags_multi.has_active_order}, has_cancelled={flags_multi.has_cancelled_order}, count={flags_multi.recent_orders_count}",
            finding="Multi-order flags collision or interference",
        )

        # 3.6 Cancelled order older than 60 days preserves customer record
        old_cancelled = [
            {
                "id": "ord_old_c",
                "created_at": (t0 - datetime.timedelta(days=75)).isoformat(),
                "financial_status": "cancelled",
                "test": False,
            }
        ]
        flags_old_c = ShopifyService.classify_order_flags(old_cancelled, window_days=60, reference_time=t0)
        old_c_ok = (
            flags_old_c.has_order_record is True
            and flags_old_c.has_recent_order is False
            and flags_old_c.recent_orders_count == 0
            and flags_old_c.has_cancelled_order is True
        )
        self.record(
            "R10-06-OLD-CANCELLED-PRESERVES-CUSTOMER",
            old_c_ok,
            f"has_order_record={flags_old_c.has_order_record}, has_recent={flags_old_c.has_recent_order}, has_cancelled={flags_old_c.has_cancelled_order}",
            finding="Old cancelled order (>60d) dropped customer record completely",
        )

    # =========================================================================
    # SUITE 4: INVARIANT R-11 (STRICT DISTINCTION: LOOKUP_UNAVAILABLE VS NO_ORDER)
    # =========================================================================
    async def run_lookup_unavailable_vs_no_order_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 4: INVARIANT R-11 (STRICT DISTINCTION: LOOKUP_UNAVAILABLE VS NO_ORDER)")
        print("=" * 80)

        engine, session_factory = await self.create_isolated_db()

        async with session_factory() as session:
            store = StoreProfile(
                id=uuid.uuid4(),
                name="Store-R11",
                brand_name="R11 Store",
                public_domain="r11.com",
                canonical_domain="r11.myshopify.com",
                status="active",
            )
            session.add(store)
            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="support@r11.com",
                encrypted_password="enc",
            )
            session.add(mailbox)
            await session.commit()

            async def simulate_enrichment_failure(exc: Exception, attempt: int = 1) -> tuple[str, str, str]:
                email_rec = IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    sender_email="test@example.com",
                    recipient_email="support@r11.com",
                    received_at=datetime.datetime.now(datetime.UTC),
                    imap_uid=int(uuid.uuid4().int % 100000),
                    uidvalidity=1,
                    status="classified",
                    classification_category="order_support",
                )
                session.add(email_rec)
                job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=store.id,
                    job_type="enrich_order",
                    status="processing",
                    attempts=attempt,
                )
                session.add(job)
                await session.commit()

                with patch.object(
                    TransactionalQueueService,
                    "_get_shopify_client_for_store",
                    return_value=AsyncMock(),
                ), patch.object(
                    ShopifyService,
                    "enrich_order_lookup",
                    side_effect=exc,
                ):
                    await TransactionalQueueService.process_order_enrichment_job(session, job.id)

                await session.refresh(email_rec)
                await session.refresh(job)
                return email_rec.customer_status, email_rec.status, job.status

            # 4.1 Proxy error -> lookup_unavailable
            c_status, e_status, j_status = await simulate_enrichment_failure(
                ShopifyProxyError("SOCKS5 connection refused")
            )
            self.record(
                "R11-01-PROXY-ERROR-LOOKUP-UNAVAILABLE",
                c_status == "lookup_unavailable" and c_status != "no_order",
                f"customer_status={c_status}, email_status={e_status}, job_status={j_status}",
                finding="Proxy error wrongly marked customer_status as no_order",
            )

            # 4.2 Transient 500 error -> lookup_unavailable
            c_status, e_status, j_status = await simulate_enrichment_failure(
                ShopifyTransientError("Shopify 500 Internal Server Error")
            )
            self.record(
                "R11-02-TRANSIENT-500-LOOKUP-UNAVAILABLE",
                c_status == "lookup_unavailable" and c_status != "no_order",
                f"customer_status={c_status}, email_status={e_status}, job_status={j_status}",
                finding="Shopify 500 error wrongly marked customer_status as no_order",
            )

            # 4.3 Rate Limit 429 error -> lookup_unavailable
            c_status, e_status, j_status = await simulate_enrichment_failure(
                ShopifyRateLimitError("Shopify rate limit exceeded")
            )
            self.record(
                "R11-03-RATE-LIMIT-429-LOOKUP-UNAVAILABLE",
                c_status == "lookup_unavailable" and c_status != "no_order",
                f"customer_status={c_status}, email_status={e_status}, job_status={j_status}",
                finding="Rate limit 429 error wrongly marked customer_status as no_order",
            )

            # 4.4 Network Timeout error -> lookup_unavailable
            c_status, e_status, j_status = await simulate_enrichment_failure(
                TimeoutError("Connection timed out after 30s")
            )
            self.record(
                "R11-04-TIMEOUT-LOOKUP-UNAVAILABLE",
                c_status == "lookup_unavailable" and c_status != "no_order",
                f"customer_status={c_status}, email_status={e_status}, job_status={j_status}",
                finding="Timeout error wrongly marked customer_status as no_order",
            )

            # 4.5 Genuine 200 OK with 0 matching orders -> must produce no_order
            email_zero = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                sender_email="unknown@example.com",
                recipient_email="support@r11.com",
                received_at=datetime.datetime.now(datetime.UTC),
                imap_uid=int(uuid.uuid4().int % 100000),
                uidvalidity=1,
                status="classified",
                classification_category="order_support",
            )
            session.add(email_zero)
            job_zero = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_zero.id,
                store_profile_id=store.id,
                job_type="enrich_order",
                status="processing",
                attempts=1,
            )
            session.add(job_zero)
            await session.commit()

            with patch.object(
                TransactionalQueueService,
                "_get_shopify_client_for_store",
                return_value=AsyncMock(),
            ), patch.object(
                ShopifyService,
                "lookup_recent_orders",
                return_value=[],
            ), patch.object(
                ShopifyService,
                "get_effective_store_policies",
                return_value={},
            ):
                await TransactionalQueueService.process_order_enrichment_job(session, job_zero.id)

            await session.refresh(email_zero)
            await session.refresh(job_zero)

            zero_match_ok = (
                email_zero.customer_status == "no_order"
                and job_zero.status == "completed"
            )
            self.record(
                "R11-05-GENUINE-200-ZERO-ORDERS-PRODUCES-NO-ORDER",
                zero_match_ok,
                f"customer_status={email_zero.customer_status}, job_status={job_zero.status}",
                finding="Successful lookup with zero orders failed to produce no_order",
            )

    # =========================================================================
    # SUITE 5: INVARIANT R-03 (PIEZAPRINT ABSOLUTE MULTI-LAYER EXCLUSION)
    # =========================================================================
    async def run_piezaprint_exclusion_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 5: INVARIANT R-03 (PIEZAPRINT ABSOLUTE MULTI-LAYER EXCLUSION)")
        print("=" * 80)

        # 5.1 Domain Validation Layer
        prohibited_pairs = [
            ("piezaprint.com", "piezaprint.myshopify.com"),
            ("PIEZAPRINT.COM", "piezaprint.myshopify.com"),
            ("store.piezaprint.com", "store-piezaprint.myshopify.com"),
            ("custom-app.com", "piezaprint.myshopify.com"),
            ("piezaprint-art.com", "art.myshopify.com"),
        ]

        all_blocked = True
        for pub, canon in prohibited_pairs:
            ok, msg = StoreWizardService.validate_domains(pub, canon)
            if ok or msg != "STORE_EXCLUDED_FROM_SYSTEM":
                all_blocked = False
                print(f"    Failed to block domain: {pub} / {canon} -> ok={ok}, msg={msg}")

        self.record(
            "R03-01-DOMAIN-VALIDATOR-EXCLUSION",
            all_blocked,
            f"Tested {len(prohibited_pairs)} domain combinations (casing, subdomains)",
            finding="Domain validator failed to block Piezaprint variations",
        )

        # 5.2 Valid non-piezaprint domain allowed
        ok_valid, msg_valid = StoreWizardService.validate_domains("wrydeco.com", "wrydeco.myshopify.com")
        self.record(
            "R03-02-VALID-STORE-PERMITTED",
            ok_valid and msg_valid == "OK",
            f"wrydeco.com validation: ok={ok_valid}, msg={msg_valid}",
            finding="Legitimate store wrydeco.com incorrectly blocked",
        )

        # 5.3 IMAP Client Piezaprint Exclusion
        imap_blocked = False
        try:
            IMAPClient(username="support@piezaprint.com", password="dummy_password")
        except PiezaprintExclusionError:
            imap_blocked = True
        except Exception as e:
            print(f"    Unexpected IMAP exception: {type(e)}: {e}")

        self.record(
            "R03-03-IMAP-CLIENT-EXCLUSION",
            imap_blocked,
            f"IMAPClient rejected support@piezaprint.com with PiezaprintExclusionError={imap_blocked}",
            finding="IMAPClient permitted support@piezaprint.com instantiation",
        )

        # 5.4 SMTP Client Piezaprint Exclusion
        smtp_blocked = False
        try:
            SMTPClient(username="SUPPORT@PIEZAPRINT.COM", password="dummy_password")
        except PiezaprintExclusionError:
            smtp_blocked = True
        except Exception as e:
            print(f"    Unexpected SMTP exception: {type(e)}: {e}")

        self.record(
            "R03-04-SMTP-CLIENT-EXCLUSION",
            smtp_blocked,
            f"SMTPClient rejected SUPPORT@PIEZAPRINT.COM with PiezaprintExclusionError={smtp_blocked}",
            finding="SMTPClient permitted SUPPORT@PIEZAPRINT.COM instantiation",
        )

    # =========================================================================
    # SUITE 6: ERROR RETRY SCHEDULE & EXHAUSTION & TERMINAL ERRORS
    # =========================================================================
    async def run_retry_schedule_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 6: ERROR RETRY SCHEDULE & EXHAUSTION (SECTION 14.1)")
        print("=" * 80)

        engine, session_factory = await self.create_isolated_db()

        # 6.1 Exponential Retry Schedule [60s, 300s, 900s, 1800s, 3600s]
        expected_delays = [60, 300, 900, 1800, 3600]
        schedule_matched = SHOPIFY_LOOKUP_RETRY_DELAYS_SECONDS == expected_delays
        self.record(
            "R14-01-RETRY-SCHEDULE-DELAYS-CONSTANT",
            schedule_matched and MAX_SHOPIFY_LOOKUP_ATTEMPTS == 5,
            f"Delays={SHOPIFY_LOOKUP_RETRY_DELAYS_SECONDS}, MaxAttempts={MAX_SHOPIFY_LOOKUP_ATTEMPTS}",
            finding="Retry delay schedule does not match required [60, 300, 900, 1800, 3600]",
        )

        async with session_factory() as session:
            store = StoreProfile(
                id=uuid.uuid4(),
                name="Store-Retry",
                brand_name="Retry Store",
                public_domain="retry.com",
                canonical_domain="retry.myshopify.com",
                status="active",
            )
            session.add(store)
            mailbox = Mailbox(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                address="support@retry.com",
                encrypted_password="enc",
            )
            session.add(mailbox)
            await session.commit()

            # Test attempts 1 through 4: scheduled delay
            attempts_delay_ok = True
            for attempt_num in range(1, 5):
                email_rec = IncomingEmail(
                    id=uuid.uuid4(),
                    store_profile_id=store.id,
                    mailbox_id=mailbox.id,
                    sender_email="buyer@example.com",
                    recipient_email="support@retry.com",
                    received_at=datetime.datetime.now(datetime.UTC),
                    imap_uid=int(uuid.uuid4().int % 100000),
                    uidvalidity=1,
                    status="classified",
                    classification_category="order_support",
                )
                session.add(email_rec)
                job = EmailJob(
                    id=uuid.uuid4(),
                    incoming_email_id=email_rec.id,
                    store_profile_id=store.id,
                    job_type="enrich_order",
                    status="processing",
                    attempts=attempt_num,
                )
                session.add(job)
                await session.commit()

                now_before = datetime.datetime.now(datetime.UTC)
                with patch.object(
                    TransactionalQueueService,
                    "_get_shopify_client_for_store",
                    return_value=AsyncMock(),
                ), patch.object(
                    ShopifyService,
                    "enrich_order_lookup",
                    side_effect=ShopifyProxyError("Transient drop"),
                ):
                    res = await TransactionalQueueService.process_order_enrichment_job(session, job.id)

                await session.refresh(job)
                await session.refresh(email_rec)

                expected_sec = expected_delays[attempt_num - 1]
                actual_delay = (job.scheduled_at - now_before).total_seconds() if job.scheduled_at else 0
                within_tolerance = abs(actual_delay - expected_sec) <= 10
                if not (res is False and job.status == "queued" and within_tolerance):
                    attempts_delay_ok = False
                    print(f"    Attempt {attempt_num} failed delay check: actual={actual_delay}s, expected={expected_sec}s")

            self.record(
                "R14-02-PROGRESSIVE-RETRY-DELAYS",
                attempts_delay_ok,
                "Verified delay schedule on attempts 1, 2, 3, 4 (1m, 5m, 15m, 30m)",
                finding="Scheduled retry delays did not match exponential intervals",
            )

            # 6.2 Retry Exhaustion at attempt 5 -> manual_review with SHOPIFY_LOOKUP_FAILED
            email_exhausted = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                sender_email="exhausted@example.com",
                recipient_email="support@retry.com",
                received_at=datetime.datetime.now(datetime.UTC),
                imap_uid=int(uuid.uuid4().int % 100000),
                uidvalidity=1,
                status="classified",
                classification_category="order_support",
            )
            session.add(email_exhausted)
            job_exhausted = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_exhausted.id,
                store_profile_id=store.id,
                job_type="enrich_order",
                status="processing",
                attempts=5,  # 5th attempt exhausted
            )
            session.add(job_exhausted)
            await session.commit()

            with patch.object(
                TransactionalQueueService,
                "_get_shopify_client_for_store",
                return_value=AsyncMock(),
            ), patch.object(
                ShopifyService,
                "enrich_order_lookup",
                side_effect=ShopifyTransientError("Shopify outage 503"),
            ):
                res_exhausted = await TransactionalQueueService.process_order_enrichment_job(session, job_exhausted.id)

            await session.refresh(email_exhausted)
            await session.refresh(job_exhausted)

            exhaustion_ok = (
                res_exhausted is False
                and job_exhausted.status == "failed"
                and email_exhausted.status == "manual_review"
                and email_exhausted.review_reason_code == "SHOPIFY_LOOKUP_FAILED"
                and email_exhausted.customer_status == "lookup_unavailable"
            )
            self.record(
                "R14-03-RETRY-EXHAUSTION-MANUAL-REVIEW",
                exhaustion_ok,
                f"job.status={job_exhausted.status}, email.status={email_exhausted.status}, code={email_exhausted.review_reason_code}",
                finding="Retry exhaustion did not transition email to manual_review with SHOPIFY_LOOKUP_FAILED",
            )

            # 6.3 Terminal Error: 401 Unauthorized halts retries immediately (0 retries)
            email_401 = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                sender_email="authfail@example.com",
                recipient_email="support@retry.com",
                received_at=datetime.datetime.now(datetime.UTC),
                imap_uid=int(uuid.uuid4().int % 100000),
                uidvalidity=1,
                status="classified",
                classification_category="order_support",
            )
            session.add(email_401)
            job_401 = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_401.id,
                store_profile_id=store.id,
                job_type="enrich_order",
                status="processing",
                attempts=1,
            )
            session.add(job_401)
            await session.commit()

            with patch.object(
                TransactionalQueueService,
                "_get_shopify_client_for_store",
                return_value=AsyncMock(),
            ), patch.object(
                ShopifyService,
                "enrich_order_lookup",
                side_effect=ShopifyAuthError("Invalid API credentials"),
            ):
                await TransactionalQueueService.process_order_enrichment_job(session, job_401.id)

            await session.refresh(email_401)
            await session.refresh(job_401)

            terminal_401_ok = (
                job_401.status == "failed"
                and email_401.status == "manual_review"
                and email_401.review_reason_code == "SHOPIFY_AUTH_FAILED"
            )
            self.record(
                "R14-04-TERMINAL-401-IMMEDIATE-HALT",
                terminal_401_ok,
                f"job.status={job_401.status}, email.status={email_401.status}, code={email_401.review_reason_code}",
                finding="401 Auth error scheduled unnecessary retries instead of halting immediately",
            )

            # 6.4 Terminal Error: 403 Forbidden halts retries immediately
            email_403 = IncomingEmail(
                id=uuid.uuid4(),
                store_profile_id=store.id,
                mailbox_id=mailbox.id,
                sender_email="perm@example.com",
                recipient_email="support@retry.com",
                received_at=datetime.datetime.now(datetime.UTC),
                imap_uid=int(uuid.uuid4().int % 100000),
                uidvalidity=1,
                status="classified",
                classification_category="order_support",
            )
            session.add(email_403)
            job_403 = EmailJob(
                id=uuid.uuid4(),
                incoming_email_id=email_403.id,
                store_profile_id=store.id,
                job_type="enrich_order",
                status="processing",
                attempts=1,
            )
            session.add(job_403)
            await session.commit()

            with patch.object(
                TransactionalQueueService,
                "_get_shopify_client_for_store",
                return_value=AsyncMock(),
            ), patch.object(
                ShopifyService,
                "enrich_order_lookup",
                side_effect=ShopifyPermissionError("Missing read_orders scope"),
            ):
                await TransactionalQueueService.process_order_enrichment_job(session, job_403.id)

            await session.refresh(email_403)
            await session.refresh(job_403)

            terminal_403_ok = (
                job_403.status == "failed"
                and email_403.status == "manual_review"
                and email_403.review_reason_code == "SHOPIFY_PERMISSION_DENIED"
            )
            self.record(
                "R14-05-TERMINAL-403-IMMEDIATE-HALT",
                terminal_403_ok,
                f"job.status={job_403.status}, email.status={email_403.status}, code={email_403.review_reason_code}",
                finding="403 Forbidden error scheduled unnecessary retries instead of halting immediately",
            )

    # =========================================================================
    # SUITE 7: INVARIANTS R-16, R-17, R-18 (PRODUCT SEARCH & POLICIES SYNC)
    # =========================================================================
    async def run_product_and_policies_suite(self) -> None:
        print("\n" + "=" * 80)
        print("SUITE 7: INVARIANTS R-16, R-17, R-18 (PRODUCT SEARCH & POLICIES SYNC)")
        print("=" * 80)

        # 7.1 Zero Catalog Mirroring DB Audit
        # Verify database schema does not have a mirrored 'products' or 'catalog' table
        engine, session_factory = await self.create_isolated_db()
        async with engine.connect() as conn:
            table_names_res = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table';")
            )
            table_names = [row[0] for row in table_names_res.fetchall()]

        has_catalog_mirror = "products" in table_names or "shopify_catalog" in table_names
        has_snapshot_tables = "shopify_order_snapshots" in table_names and "shopify_product_snapshots" in table_names
        self.record(
            "R16-01-ZERO-CATALOG-MIRRORING-AUDIT",
            not has_catalog_mirror and has_snapshot_tables,
            f"Tables checked: {len(table_names)}, No full catalog table={not has_catalog_mirror}, Snapshots={has_snapshot_tables}",
            finding="Found mirrored products table violating Zero Catalog Mirroring Invariant R-16",
        )

        # 7.2 Live product search zero-result warning PRODUCT_NOT_RESOLVED
        mock_client = AsyncMock()
        mock_client.execute_graphql.return_value = {"data": {"products": {"edges": []}}}
        empty_search_snap = await ShopifyService.search_products_live(mock_client, ["NonExistentLamp"])
        empty_ok = (
            empty_search_snap.product_resolved is False
            and empty_search_snap.matched_count == 0
            and "PRODUCT_NOT_RESOLVED" in empty_search_snap.warning_codes
        )
        self.record(
            "R16-02-PRODUCT-NOT-RESOLVED-WARNING",
            empty_ok,
            f"product_resolved={empty_search_snap.product_resolved}, warnings={empty_search_snap.warning_codes}",
            finding="Zero products found did not trigger PRODUCT_NOT_RESOLVED warning",
        )

        # 7.3 Policies HTML Sanitization & 64-hex SHA-256 Hash
        raw_dirty_html = """
        <h2>Refund Policy</h2>
        <script>alert('malicious')</script>
        <p>You can return items within 30 days.</p>
        <iframe src="http://evil.com"></iframe>
        <a href="javascript:void(0)" onclick="stealCookies()">Click here</a>
        """
        clean_html = sanitize_policy_html(raw_dirty_html)
        clean_text = normalize_policy_text(clean_html)
        content_hash = compute_content_hash(clean_text)

        sanitization_clean = (
            "<script" not in clean_html
            and "<iframe" not in clean_html
            and "onclick" not in clean_html
            and "stealCookies" not in clean_html
        )
        hash_valid = len(content_hash) == 64 and all(c in "0123456789abcdef" for c in content_hash)
        self.record(
            "R17-01-POLICY-SANITIZATION-AND-SHA256",
            sanitization_clean and hash_valid,
            f"Scripts removed={sanitization_clean}, SHA-256 length={len(content_hash)} (64-hex valid={hash_valid})",
            finding="Policy sanitization leaked malicious tags or generated invalid hash",
        )

        # 7.4 Stale Draft Detection (Invariant R-18)
        current_hashes = {
            "REFUND": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "PRIVACY": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }
        draft_hashes_stale = {
            "REFUND": "old_hash_11111111111111111111111111111111111111111111111111111111",
            "PRIVACY": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }
        draft_hashes_fresh = dict(current_hashes)

        stale_check = ShopifyService.check_draft_policy_freshness(draft_hashes_stale, current_hashes)
        fresh_check = ShopifyService.check_draft_policy_freshness(draft_hashes_fresh, current_hashes)

        stale_ok = (
            stale_check.is_stale is True
            and any(d["policy_type"] == "REFUND" for d in stale_check.stale_details)
            and stale_check.stale_reason == "POLICY_UPDATED"
        )
        fresh_ok = (
            fresh_check.is_stale is False
            and len(fresh_check.stale_details) == 0
        )
        self.record(
            "R18-01-STALE-DRAFT-POLICY-DETECTION",
            stale_ok and fresh_ok,
            f"Stale detected={stale_ok}, Fresh detected={fresh_ok}",
            finding="Failed to detect stale draft when policy hash changed",
        )

    async def run_all(self) -> int:
        print("=" * 80)
        print("MAIL AGENT CHALLENGER EMPIRICAL VERIFICATION HARNESS — PHASE 5")
        print("=" * 80)

        await self.run_fail_closed_proxy_suite()
        await self.run_email_norm_and_60day_suite()
        await self.run_cancelled_refunded_matrix_suite()
        await self.run_lookup_unavailable_vs_no_order_suite()
        await self.run_piezaprint_exclusion_suite()
        await self.run_retry_schedule_suite()
        await self.run_product_and_policies_suite()

        print("\n" + "=" * 80)
        print("CHALLENGER EMPIRICAL SUMMARY")
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


if __name__ == "__main__":
    challenger = EmpiricalPhase5Challenger()
    exit_code = asyncio.run(challenger.run_all())
    sys.exit(exit_code)
