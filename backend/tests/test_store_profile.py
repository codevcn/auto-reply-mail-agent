"""Integration tests for StoreProfile, ProxyProfile, ShopifyConnection, and Mailbox."""

import uuid

import pytest
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models.store import Mailbox, ProxyProfile, ShopifyConnection
from app.store.schemas import StoreProfileCreate
from app.store.services import StoreWizardService
from sqlalchemy import select


@pytest.mark.asyncio
async def test_store_and_credentials_envelope_encryption(db_session):
    raw_proxy_pass = "socks5_raw_pass_secret123"
    raw_shopify_secret = "shpss_raw_client_secret_998877"
    raw_mail_pass = "imap_mail_password_332211"

    # 1. Create Proxy Profile
    proxy = ProxyProfile(
        id=uuid.uuid4(),
        name="US Proxy Test 1",
        protocol="socks5",
        host="198.51.100.24",
        port=1080,
        encrypted_password=encrypt_secret(raw_proxy_pass),
    )
    db_session.add(proxy)
    await db_session.flush()

    assert proxy.encrypted_password.startswith("enc_v1:")
    assert raw_proxy_pass not in proxy.encrypted_password
    assert decrypt_secret(proxy.encrypted_password) == raw_proxy_pass

    # 2. Create Store Profile via Wizard Service
    create_dto = StoreProfileCreate(
        name="Wrydeco US Store",
        brand_name="Wrydeco",
        public_domain="wrydeco.com",
        canonical_domain="wrydeco.myshopify.com",
        mailbox_address="support@wrydeco.com",
        mailbox_password=raw_mail_pass,
        brand_voice="Friendly, empathetic",
        email_signature="Best regards,\nWrydeco Support",
        proxy_profile_id=str(proxy.id),
        shopify_client_id="shpss_client_id_wrydeco",
        shopify_client_secret=raw_shopify_secret,
    )
    store = await StoreWizardService.create_store_profile(db_session, create_dto)
    await db_session.commit()

    # Re-fetch from DB
    loaded_store = await StoreWizardService.get_store_profile(db_session, store.id)
    assert loaded_store is not None
    assert loaded_store.status == "draft"
    assert loaded_store.proxy_profile_id == proxy.id

    # Verify mailbox credentials encryption
    assert len(loaded_store.mailboxes) == 1
    mb = loaded_store.mailboxes[0]
    assert mb.encrypted_password.startswith("enc_v1:")
    assert raw_mail_pass not in mb.encrypted_password
    assert decrypt_secret(mb.encrypted_password) == raw_mail_pass

    # Verify Shopify connection credentials encryption
    conn = loaded_store.shopify_connection
    assert conn is not None
    assert conn.encrypted_client_secret.startswith("enc_v1:")
    assert raw_shopify_secret not in conn.encrypted_client_secret
    assert decrypt_secret(conn.encrypted_client_secret) == raw_shopify_secret


@pytest.mark.asyncio
async def test_store_cascade_deletion(db_session):
    create_dto = StoreProfileCreate(
        name="Cascade Test Store",
        brand_name="Cascade",
        public_domain="chillgen.com",
        canonical_domain="chillgen.myshopify.com",
        mailbox_address="support@chillgen.com",
        mailbox_password="pass",
        shopify_client_id="id1",
        shopify_client_secret="secret1",
    )
    store = await StoreWizardService.create_store_profile(db_session, create_dto)
    store_id = store.id

    # Delete store profile
    await db_session.delete(store)
    await db_session.commit()

    # Verify subordinated records were cascade deleted
    mb_res = await db_session.execute(select(Mailbox).where(Mailbox.store_profile_id == store_id))
    assert mb_res.scalar_one_or_none() is None

    conn_res = await db_session.execute(
        select(ShopifyConnection).where(ShopifyConnection.store_profile_id == store_id)
    )
    assert conn_res.scalar_one_or_none() is None
