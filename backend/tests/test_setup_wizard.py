"""Unit and API tests for Store Setup Wizard and Store Profile activation."""

import pytest
from app.store.schemas import StoreProfileCreate
from app.store.services import (
    SUPPORTED_STORE_DOMAINS,
    StoreWizardService,
)


def test_domain_separation_and_canonical_myshopify():
    """TC-WIZARD-01: Public domain and canonical myshopify domain are separated."""
    ok, msg = StoreWizardService.validate_domains("wrydeco.com", "wrydeco.myshopify.com")
    assert ok is True
    assert msg == "OK"

    bad_ok, bad_msg = StoreWizardService.validate_domains("wrydeco.com", "wrydeco.com")
    assert bad_ok is False
    assert bad_msg == "CANONICAL_DOMAIN_MUST_BE_MYSHOPIFY"


def test_piezaprint_exclusion_rule():
    """TC-WIZARD-02: support@piezaprint.com is strictly excluded from configuration."""
    ok, msg = StoreWizardService.validate_domains("piezaprint.com", "piezaprint.myshopify.com")
    assert ok is False
    assert msg == "STORE_EXCLUDED_FROM_SYSTEM"

    ok_sub, msg_sub = StoreWizardService.validate_domains("store.piezaprint.com", "piezaprint.myshopify.com")
    assert ok_sub is False
    assert msg_sub == "STORE_EXCLUDED_FROM_SYSTEM"


def test_all_four_supported_stores_accepted():
    """TC-WIZARD-03: All 4 target stores are accepted."""
    for pub, canon in SUPPORTED_STORE_DOMAINS.items():
        ok, msg = StoreWizardService.validate_domains(pub, canon)
        assert ok is True, f"Store {pub} must be accepted"
        assert msg == "OK"


@pytest.mark.asyncio
async def test_activation_prerequisites_enforced(db_session):
    """TC-WIZARD-04: Cannot activate store profile without passing Mailbox, Proxy, and Shopify tests."""
    create_dto = StoreProfileCreate(
        name="Activation Test Store",
        brand_name="ActivationTest",
        public_domain="wrydeco.com",
        canonical_domain="wrydeco.myshopify.com",
        mailbox_address="support@wrydeco.com",
    )
    store = await StoreWizardService.create_store_profile(db_session, create_dto)
    assert store.status == "draft"

    # 1. Try to activate without proxy test
    ok, err = await StoreWizardService.activate_profile(
        db_session, store.id, mailbox_tested=True, proxy_tested=False, shopify_tested=True
    )
    assert ok is False
    assert err == "ALL_CONNECTION_TESTS_REQUIRED_BEFORE_ACTIVATION"
    assert store.status == "draft"

    # 2. Try to activate without shopify test
    ok2, err2 = await StoreWizardService.activate_profile(
        db_session, store.id, mailbox_tested=True, proxy_tested=True, shopify_tested=False
    )
    assert ok2 is False
    assert err2 == "ALL_CONNECTION_TESTS_REQUIRED_BEFORE_ACTIVATION"

    # 3. Activate with all tests passing
    ok_full, msg = await StoreWizardService.activate_profile(
        db_session, store.id, mailbox_tested=True, proxy_tested=True, shopify_tested=True
    )
    assert ok_full is True
    assert msg == "ACTIVATED"
    assert store.status == "active"
    assert store.activation_baseline_uid is not None
    assert store.uid_validity is not None


@pytest.mark.asyncio
async def test_api_store_creation_and_activation_flow(client, bootstrap_user):
    _ = await bootstrap_user(username="wizard_admin", role="admin")
    login_resp = await client.post(
        "/api/auth/login",
        json={"username": "wizard_admin", "password": "SecretPassword123!"},
    )
    assert login_resp.status_code == 200

    # 1. Create Store via API
    create_payload = {
        "name": "API Store Test",
        "brand_name": "APITest",
        "public_domain": "jeminise.com",
        "canonical_domain": "jeminise.myshopify.com",
        "mailbox_address": "support@jeminise.com",
        "mailbox_password": "mail_secret_123",
        "shopify_client_id": "client_id_jeminise",
        "shopify_client_secret": "client_secret_jeminise_123",
    }
    create_res = await client.post("/api/stores", json=create_payload)
    assert create_res.status_code == 201
    store_data = create_res.json()
    store_id = store_data["id"]
    assert store_data["status"] == "draft"

    # 2. Try activating with incomplete tests
    fail_act = await client.post(
        f"/api/stores/{store_id}/activate",
        json={"mailbox_tested": True, "proxy_tested": False, "shopify_tested": True},
    )
    assert fail_act.status_code == 400
    assert "ALL_CONNECTION_TESTS_REQUIRED_BEFORE_ACTIVATION" in fail_act.json()["detail"]

    # 3. Activate with all tests passing
    succ_act = await client.post(
        f"/api/stores/{store_id}/activate",
        json={"mailbox_tested": True, "proxy_tested": True, "shopify_tested": True},
    )
    assert succ_act.status_code == 200
    act_data = succ_act.json()
    assert act_data["success"] is True
    assert act_data["status"] == "active"
    assert act_data["message"] == "Store profile activated"
