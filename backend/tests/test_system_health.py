"""Unit and integration tests for System Health, Observability, and Probes (Phase 7)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from app.db.models.audit import AuditEvent
from app.db.models.email import IncomingEmail
from app.db.models.store import Mailbox, ProxyProfile, ShopifyConnection, StoreProfile
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_liveness_and_readiness_probes(client):
    """Verifies unauthenticated container probes /health/live and /health/ready."""
    # 1. Liveness Probe
    live_res = await client.get("/health/live")
    assert live_res.status_code == 200
    live_data = live_res.json()
    assert live_data["status"] == "alive"
    assert "uptime_seconds" in live_data
    assert live_data["uptime_seconds"] >= 0

    # 2. Readiness Probe
    ready_res = await client.get("/health/ready")
    assert ready_res.status_code == 200
    ready_data = ready_res.json()
    assert ready_data["status"] == "ready"
    assert ready_data["database"]["status"] == "connected"
    assert "latency_ms" in ready_data["database"]


@pytest.mark.asyncio
async def test_health_summary_authenticated_and_structure(
    client, bootstrap_user, db_session: AsyncSession
):
    """Verifies GET /api/system/health-summary authentication and complete metrics payload."""
    # Unauthenticated request must return 401
    unauth_res = await client.get("/api/system/health-summary")
    assert unauth_res.status_code == 401

    # Authenticate admin user
    await bootstrap_user("health_admin", "Password123!")
    login_res = await client.post(
        "/api/auth/login",
        json={"username": "health_admin", "password": "Password123!"},
    )
    assert login_res.status_code == 200

    # Seed Proxy
    proxy = ProxyProfile(
        id=uuid.uuid4(),
        name="Production US Proxy",
        host="198.54.120.35",
        port=1080,
        enabled=True,
        last_test_status="passed",
        last_exit_ip="198.54.120.35",
        last_latency_ms=145,
    )
    db_session.add(proxy)

    # Seed 2 stores (Wrydeco and Chillgen)
    wrydeco = StoreProfile(
        id=uuid.uuid4(),
        name="Wrydeco",
        brand_name="Wrydeco US",
        public_domain="wrydeco.com",
        status="active",
    )
    chillgen = StoreProfile(
        id=uuid.uuid4(),
        name="Chillgen",
        brand_name="Chillgen Apparel",
        public_domain="chillgen.com",
        status="active",
    )
    db_session.add_all([wrydeco, chillgen])
    await db_session.flush()

    mb_wrydeco = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=wrydeco.id,
        address="support@wrydeco.com",
        encrypted_password="enc_password",
        status="active",
    )
    db_session.add(mb_wrydeco)

    sh_wrydeco = ShopifyConnection(
        id=uuid.uuid4(),
        store_profile_id=wrydeco.id,
        shop_domain="wrydeco.myshopify.com",
        client_id="sh_cid",
        encrypted_client_secret="enc_sec",
        auth_status="authenticated",
    )
    db_session.add(sh_wrydeco)

    # Seed an email in queue
    now_utc = datetime.datetime.now(datetime.UTC)
    email = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=wrydeco.id,
        mailbox_id=mb_wrydeco.id,
        imap_uid=1001,
        uidvalidity=1,
        sender_email="buyer@example.com",
        recipient_email="support@wrydeco.com",
        received_at=now_utc - datetime.timedelta(hours=2),
        created_at=now_utc - datetime.timedelta(hours=2),
        status="pending_approval",
        classification_category="product_inquiry",
    )
    db_session.add(email)
    await db_session.commit()

    # Query health summary
    res = await client.get("/api/system/health-summary")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] in ("healthy", "degraded", "down")
    assert "uptime_seconds" in data
    assert data["database"]["status"] == "connected"
    assert data["worker"]["status"] in ("alive", "stale", "dead")

    # Proxy check
    assert data["proxy"]["status"] == "passed"
    assert "198.54.***.***:1080" in data["proxy"]["host_masked"]
    assert data["proxy"]["last_exit_ip"] == "198.54.120.35"

    # Queue counters check
    queues = data["queues"]
    assert queues["ready_to_review"] >= 1
    assert queues["product_inquiry"] >= 1
    assert queues["total_unprocessed"] >= 1
    assert queues["oldest_unreviewed_age_seconds"] is not None
    assert queues["oldest_unreviewed_age_seconds"] > 7000  # ~2 hours

    # Stores check
    store_names = [st["name"] for st in data["stores"]]
    assert "Wrydeco" in store_names
    assert "Chillgen" in store_names


@pytest.mark.asyncio
async def test_audit_events_feed_endpoint(client, bootstrap_user, db_session: AsyncSession):
    """Verifies GET /api/system/audit returns safe audit history."""
    user = await bootstrap_user("audit_user", "SafePass123!")
    login_res = await client.post(
        "/api/auth/login",
        json={"username": "audit_user", "password": "SafePass123!"},
    )
    assert login_res.status_code == 200

    # Create audit events
    audit1 = AuditEvent(
        id=uuid.uuid4(),
        actor_user_id=user.id,
        event_type="AUTH_LOGIN",
        safe_change_summary={"action": "login_success"},
    )
    audit2 = AuditEvent(
        id=uuid.uuid4(),
        actor_user_id=user.id,
        event_type="STORE_ACTIVATE",
        safe_change_summary={"store": "Wrydeco", "status": "active"},
    )
    db_session.add_all([audit1, audit2])
    await db_session.commit()

    res = await client.get("/api/system/audit?limit=10")
    assert res.status_code == 200
    data = res.json()

    assert data["total"] >= 2
    items = data["items"]
    assert len(items) >= 2
    event_types = [item["event_type"] for item in items]
    assert "STORE_ACTIVATE" in event_types
    assert "AUTH_LOGIN" in event_types
    assert items[0]["actor_username"] == "audit_user"
