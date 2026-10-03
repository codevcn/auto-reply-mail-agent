"""Authoritative Test Fixtures and Domain Simulators for Mail Agent E2E Testing.
Derived directly from plan-build-mail-agent.md and PROJECT.md.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple


# --- Domain Constants & Contracts ---
SUPPORTED_ATTACHMENT_MIMES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
    "application/pdf": b"%PDF-",
    "text/plain": None,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK\x03\x04",
}

MAX_SINGLE_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024   # 25MB
MAX_ATTACHMENT_COUNT = 10
ORDER_LOOKUP_WINDOW_DAYS = 60
RETENTION_CAP_DAYS = 120

SUPPORTED_STORE_DOMAINS = {
    "wrydeco.com": "wrydeco.myshopify.com",
    "chillgen.com": "chillgen.myshopify.com",
    "preaureum.com": "preaureum.myshopify.com",
    "jeminise.com": "jeminise.myshopify.com",
}
EXCLUDED_STORE_DOMAINS = {"piezaprint.com"}

POLICY_TYPES = ["REFUND", "PRIVACY", "TERMS", "SHIPPING", "CONTACT", "LEGAL"]


# --- Dataclasses ---
@dataclasses.dataclass
class UserAccount:
    id: str
    username: str
    password_hash: str
    is_active: bool
    role: str = "admin"
    created_at: datetime.datetime = dataclasses.field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))


@dataclasses.dataclass
class StoreProfile:
    id: str
    name: str
    public_domain: str
    canonical_domain: str
    mailbox_address: str
    is_active: bool
    activation_baseline_uid: int
    uid_validity: int
    row_version: int = 1
    policy_hashes: Dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class EmailAttachment:
    filename: str
    content_type: str
    size_bytes: int
    raw_bytes: bytes
    is_password_protected: bool = False


@dataclasses.dataclass
class InboundEmail:
    id: str
    mailbox_address: str
    imap_uid: int
    uid_validity: int
    from_address: str
    subject: str
    body_text: str
    headers: Dict[str, str]
    attachments: List[EmailAttachment] = dataclasses.field(default_factory=list)
    received_at: datetime.datetime = dataclasses.field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))


@dataclasses.dataclass
class ClassificationResult:
    is_spam: bool
    order_status: str  # has_order, no_order, uncertain
    intent: str        # product_inquiry, order_support, complaint, return_or_refund, partnership, other, uncertain
    detected_language: str
    confidence: float
    reasoning: str
    requires_manual_review: bool
    review_reason_code: Optional[str] = None


@dataclasses.dataclass
class ReplyDraft:
    id: str
    work_item_id: str
    version_num: int
    subject: str
    body_text: str
    language: str
    policy_hash_used: str
    is_stale: bool = False
    warning_codes: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class DeliveryAttempt:
    id: str
    draft_id: str
    idempotency_key: str
    status: str  # sending, sent, delivery_unknown, failed
    message_id: str
    in_reply_to: str
    references: str
    sent_at: Optional[datetime.datetime] = None


# --- Test Environment Simulators (Opaque Box) ---

class MockAuthService:
    """Simulates Authentication & User Lockout Invariants (R-27, R-28, R-35)."""
    def __init__(self):
        self.users: Dict[str, UserAccount] = {}
        self.sessions: Dict[str, str] = {}  # session_id -> user_id
        self.audit_log: List[Dict[str, Any]] = []

    def bootstrap_admin(self, username: str, password_hash: str) -> UserAccount:
        user_id = str(uuid.uuid4())
        normalized = username.strip().lower()
        user = UserAccount(id=user_id, username=normalized, password_hash=password_hash, is_active=True)
        self.users[user_id] = user
        return user

    def login(self, username: str, password_valid: bool) -> Tuple[bool, Optional[str], Optional[str]]:
        normalized = username.strip().lower()
        matched = next((u for u in self.users.values() if u.username == normalized and u.is_active), None)
        if not matched or not password_valid:
            self.audit_log.append({"event": "LOGIN_FAILED", "username": normalized})
            return False, None, "INVALID_CREDENTIALS"
        
        session_id = f"sess_{uuid.uuid4().hex}"
        self.sessions[session_id] = matched.id
        self.audit_log.append({"event": "LOGIN_SUCCESS", "user_id": matched.id})
        return True, session_id, None

    def logout(self, session_id: str) -> bool:
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

    def disable_user(self, actor_user_id: str, target_user_id: str) -> Tuple[bool, Optional[str]]:
        # Invariant 1: Block self-disable
        if actor_user_id == target_user_id:
            return False, "SELF_DISABLE_NOT_ALLOWED"
        
        # Invariant 2: Block disabling the last active user
        active_users = [u for u in self.users.values() if u.is_active]
        target = self.users.get(target_user_id)
        if not target or not target.is_active:
            return False, "USER_ALREADY_INACTIVE"
        
        if len(active_users) <= 1:
            return False, "LAST_ACTIVE_USER_REQUIRED"
        
        target.is_active = False
        # Revoke all sessions for target
        self.sessions = {sid: uid for sid, uid in self.sessions.items() if uid != target_user_id}
        self.audit_log.append({"event": "USER_DISABLED", "actor": actor_user_id, "target": target_user_id})
        return True, None


class MockProxyClient:
    """Simulates SOCKS5 Proxy & Fail-Closed Transport (R-13, R-14, R-15)."""
    def __init__(self, is_alive: bool = True, remote_dns_enabled: bool = True):
        self.is_alive = is_alive
        self.remote_dns_enabled = remote_dns_enabled
        self.exit_ip = "198.51.100.24"  # Mock US Exit IP
        self.direct_connection_attempted = False

    def test_connection(self) -> Dict[str, Any]:
        if not self.is_alive:
            return {"success": False, "error": "PROXY_CONNECTION_FAILED"}
        if not self.remote_dns_enabled:
            return {"success": False, "error": "PROXY_DNS_RESOLUTION_FAILED"}
        return {
            "success": True,
            "exit_ip": self.exit_ip,
            "country": "US",
            "latency_ms": 42
        }

    def execute_shopify_request(self, endpoint: str, headers: Dict[str, str], allow_direct_fallback: bool = False) -> Dict[str, Any]:
        if not self.is_alive:
            if allow_direct_fallback:
                self.direct_connection_attempted = True
                raise RuntimeError("FAIL-CLOSED INVARIANT VIOLATED: Direct connection attempted!")
            raise ConnectionError("ProxyConnectionError: SOCKS5 tunnel is unreachable. Fail-closed enforced.")
        return {"status_code": 200, "data": {"endpoint": endpoint}}


class MockShopifyService:
    """Simulates Shopify Admin API over Proxy (60-day orders, live products, 6 policies)."""
    def __init__(self, proxy: MockProxyClient):
        self.proxy = proxy
        self.orders_db: List[Dict[str, Any]] = []
        self.policies_db: Dict[str, str] = {
            "REFUND": "Standard 30-day refund policy.",
            "PRIVACY": "We respect customer privacy.",
            "TERMS": "Standard terms of service.",
            "SHIPPING": "Ships within 2-3 business days.",
            "CONTACT": "Contact us at support@wrydeco.com",
            "LEGAL": "All rights reserved.",
        }
        self.products_db = [
            {"id": "prod_1", "title": "Minimalist Wall Art", "inventory": 15, "price": 49.99},
            {"id": "prod_2", "title": "Ceramic Table Lamp", "inventory": 4, "price": 89.00},
        ]
        self.simulated_outage = False
        self.retry_count = 0

    def get_order_by_customer_email(self, email: str, reference_time: datetime.datetime) -> Dict[str, Any]:
        # Enforce proxy
        self.proxy.execute_shopify_request(f"/orders.json?email={email}", {})

        if self.simulated_outage:
            self.retry_count += 1
            raise RuntimeError("Shopify 500 Internal Server Error (Transient)")

        normalized_email = email.strip().lower()
        # Find orders within 60 days
        window_start = reference_time - datetime.timedelta(days=ORDER_LOOKUP_WINDOW_DAYS)
        matched_orders = [
            o for o in self.orders_db
            if o["customer_email"].strip().lower() == normalized_email
        ]

        recent_orders = [o for o in matched_orders if o["created_at"] >= window_start]
        has_cancelled = any(o.get("financial_status") == "cancelled" or o.get("cancelled_at") for o in matched_orders)
        has_refunded = any(o.get("financial_status") == "refunded" for o in matched_orders)

        return {
            "has_order_record": len(matched_orders) > 0,
            "has_recent_order": len(recent_orders) > 0,
            "recent_orders_count": len(recent_orders),
            "has_cancelled_order": has_cancelled,
            "has_refunded_order": has_refunded,
            "latest_order": recent_orders[0] if recent_orders else (matched_orders[0] if matched_orders else None),
        }

    def search_product(self, query: str) -> Optional[Dict[str, Any]]:
        self.proxy.execute_shopify_request(f"/products.json?q={query}", {})
        q_lower = query.lower()
        for prod in self.products_db:
            if q_lower in prod["title"].lower():
                return prod
        return None

    def get_policies(self) -> Dict[str, Dict[str, str]]:
        self.proxy.execute_shopify_request("/policies.json", {})
        result = {}
        for ptype, content in self.policies_db.items():
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            result[ptype] = {"content": content, "hash": content_hash}
        return result


class MockMailEngine:
    """Simulates Mail Ingestion (IMAP IDLE/Reconciliation) & SMTP STARTTLS Delivery."""
    def __init__(self):
        self.mailbox_emails: List[InboundEmail] = []
        self.sent_folder_copies: List[Dict[str, Any]] = []
        self.delivery_attempts: Dict[str, DeliveryAttempt] = {}  # idempotency_key -> attempt
        self.seen_flags: Set[int] = set()

    def receive_email(self, email: InboundEmail):
        self.mailbox_emails.append(email)

    def ingest_new_messages(self, baseline_uid: int, uid_validity: int) -> List[InboundEmail]:
        # Ingestion contract: Only ingest messages with UID > baseline_uid AND matching uid_validity
        # Must not change \Seen flag
        new_items = [
            m for m in self.mailbox_emails
            if m.imap_uid > baseline_uid and m.uid_validity == uid_validity
        ]
        return new_items

    def send_reply(
        self,
        draft_id: str,
        idempotency_key: str,
        original_email: InboundEmail,
        reply_body: str,
        simulate_network_timeout: bool = False
    ) -> DeliveryAttempt:
        # Check idempotency key first
        if idempotency_key in self.delivery_attempts:
            existing = self.delivery_attempts[idempotency_key]
            if existing.status in ("sending", "sent"):
                return existing

        message_id = f"<{uuid.uuid4().hex}@wrydeco.com>"
        orig_msg_id = original_email.headers.get("Message-ID", f"<orig-{original_email.imap_uid}@client.com>")
        orig_refs = original_email.headers.get("References", "")
        references = f"{orig_refs} {orig_msg_id}".strip()

        attempt = DeliveryAttempt(
            id=str(uuid.uuid4()),
            draft_id=draft_id,
            idempotency_key=idempotency_key,
            status="sending",
            message_id=message_id,
            in_reply_to=orig_msg_id,
            references=references,
        )
        self.delivery_attempts[idempotency_key] = attempt

        if simulate_network_timeout:
            # Ambiguous delivery condition (Section 17)
            attempt.status = "delivery_unknown"
            return attempt

        # Success path
        attempt.status = "sent"
        attempt.sent_at = datetime.datetime.now(datetime.timezone.utc)

        # Copy to IMAP Sent folder
        self.sent_folder_copies.append({
            "message_id": message_id,
            "in_reply_to": orig_msg_id,
            "body": reply_body,
            "folder": "Sent",
            "appended_at": attempt.sent_at,
        })
        return attempt
