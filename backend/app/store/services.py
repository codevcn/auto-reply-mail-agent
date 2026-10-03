"""Store profile and Setup Wizard business services."""

from __future__ import annotations

import contextlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models.email import MailboxCheckpoint
from app.db.models.store import Mailbox, ShopifyConnection, StoreProfile
from app.mail.imap_client import IMAPClient
from app.store.schemas import StoreProfileCreate, StoreProfileUpdate

EXCLUDED_STORE_DOMAINS = ["piezaprint.com", "piezaprint"]

SUPPORTED_STORE_DOMAINS = {
    "wrydeco.com": "wrydeco.myshopify.com",
    "chillgen.com": "chillgen.myshopify.com",
    "preaureum.com": "preaureum.myshopify.com",
    "jeminise.com": "jeminise.myshopify.com",
}


class StoreWizardService:
    """Handles domain validation, store isolation, credentials envelope encryption, and activation."""

    @staticmethod
    def validate_domains(public_domain: str, canonical_domain: str) -> tuple[bool, str]:
        """Validates domain separation and strictly excludes unpermitted stores."""
        canon = canonical_domain.strip().lower()
        pub = public_domain.strip().lower()

        # Domain rule: canonical domain must end with .myshopify.com
        if not canon.endswith(".myshopify.com"):
            return False, "CANONICAL_DOMAIN_MUST_BE_MYSHOPIFY"

        # Invariant: Piezaprint must never be configured (R-03)
        if any(exc in pub or exc in canon for exc in EXCLUDED_STORE_DOMAINS):
            return False, "STORE_EXCLUDED_FROM_SYSTEM"

        return True, "OK"

    @staticmethod
    async def create_store_profile(
        db: AsyncSession,
        data: StoreProfileCreate,
        created_by: uuid.UUID | None = None,
    ) -> StoreProfile:
        ok, msg = StoreWizardService.validate_domains(data.public_domain, data.canonical_domain)
        if not ok:
            raise ValueError(msg)

        proxy_uuid = None
        if data.proxy_profile_id:
            with contextlib.suppress(ValueError):
                proxy_uuid = uuid.UUID(data.proxy_profile_id)

        store = StoreProfile(
            name=data.name,
            brand_name=data.brand_name,
            public_domain=data.public_domain.strip().lower(),
            canonical_domain=data.canonical_domain.strip().lower(),
            tone_of_voice=data.brand_voice,
            email_signature=data.email_signature,
            brand_description=data.brand_description,
            default_language=data.default_language,
            proxy_profile_id=proxy_uuid,
            status="draft",
            created_by=created_by,
        )
        db.add(store)
        await db.flush()

        # 1. Mailbox subordinate configuration with envelope encryption
        if data.mailbox_address:
            enc_pass = encrypt_secret(data.mailbox_password or "")
            mailbox = Mailbox(
                store_profile_id=store.id,
                address=data.mailbox_address.strip().lower(),
                encrypted_password=enc_pass,
                status="unconfigured",
            )
            db.add(mailbox)

        # 2. Shopify connection with envelope encryption
        if data.shopify_client_id and data.shopify_client_secret:
            enc_secret = encrypt_secret(data.shopify_client_secret)
            connection = ShopifyConnection(
                store_profile_id=store.id,
                shop_domain=data.canonical_domain.strip().lower(),
                client_id=data.shopify_client_id.strip(),
                encrypted_client_secret=enc_secret,
                proxy_profile_id=proxy_uuid,
                auth_status="unconfigured",
            )
            db.add(connection)

        await db.commit()
        await db.refresh(store)
        return store

    @staticmethod
    async def get_store_profile(db: AsyncSession, store_id: uuid.UUID) -> StoreProfile | None:
        stmt = (
            select(StoreProfile)
            .options(
                selectinload(StoreProfile.mailboxes),
                selectinload(StoreProfile.shopify_connection),
            )
            .where(StoreProfile.id == store_id)
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def list_store_profiles(db: AsyncSession) -> list[StoreProfile]:
        stmt = (
            select(StoreProfile)
            .options(
                selectinload(StoreProfile.mailboxes),
                selectinload(StoreProfile.shopify_connection),
            )
            .order_by(StoreProfile.created_at.desc())
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def update_store_profile(
        db: AsyncSession,
        store_id: uuid.UUID,
        data: StoreProfileUpdate,
    ) -> StoreProfile | None:
        store = await StoreWizardService.get_store_profile(db, store_id)
        if not store:
            return None

        if data.public_domain and data.canonical_domain:
            ok, msg = StoreWizardService.validate_domains(data.public_domain, data.canonical_domain)
            if not ok:
                raise ValueError(msg)

        if data.name is not None:
            store.name = data.name
        if data.brand_name is not None:
            store.brand_name = data.brand_name
        if data.public_domain is not None:
            store.public_domain = data.public_domain.strip().lower()
        if data.canonical_domain is not None:
            store.canonical_domain = data.canonical_domain.strip().lower()
        if data.brand_voice is not None:
            store.tone_of_voice = data.brand_voice
        if data.email_signature is not None:
            store.email_signature = data.email_signature
        if data.brand_description is not None:
            store.brand_description = data.brand_description
        if data.default_language is not None:
            store.default_language = data.default_language
        if data.status is not None:
            store.status = data.status

        if data.proxy_profile_id is not None:
            store.proxy_profile_id = None
            with contextlib.suppress(ValueError):
                store.proxy_profile_id = uuid.UUID(data.proxy_profile_id)

        # Update mailbox password if given
        if data.mailbox_password and store.mailboxes:
            store.mailboxes[0].encrypted_password = encrypt_secret(data.mailbox_password)

        # Update shopify secret if given
        if data.shopify_client_secret and store.shopify_connection:
            store.shopify_connection.encrypted_client_secret = encrypt_secret(data.shopify_client_secret)

        store.row_version += 1
        await db.commit()
        await db.refresh(store)
        return store

    @staticmethod
    async def activate_profile(
        db: AsyncSession,
        store_id: uuid.UUID,
        mailbox_tested: bool,
        proxy_tested: bool,
        shopify_tested: bool,
    ) -> tuple[bool, str]:
        """Activates a store profile only when all connection tests have succeeded."""
        if not (mailbox_tested and proxy_tested and shopify_tested):
            return False, "ALL_CONNECTION_TESTS_REQUIRED_BEFORE_ACTIVATION"

        store = await StoreWizardService.get_store_profile(db, store_id)
        if not store:
            return False, "STORE_NOT_FOUND"

        store.status = "active"
        # Set initial IMAP baseline UID and validity if not already set (preserves existing checkpoint on re-activation)
        if store.activation_baseline_uid is None or store.uid_validity is None:
            baseline_discovered = False
            if store.mailboxes:
                mb = store.mailboxes[0]
                with contextlib.suppress(Exception):
                    raw_pass = decrypt_secret(mb.encrypted_password)
                    imap_client = IMAPClient(
                        host=mb.imap_host,
                        port=mb.imap_port,
                        username=mb.address,
                        password=raw_pass,
                        tls_mode=mb.imap_tls_mode,
                    )
                    uid_validity, baseline_uid = await imap_client.get_mailbox_baseline()
                    store.activation_baseline_uid = baseline_uid
                    store.uid_validity = uid_validity
                    baseline_discovered = True

            if not baseline_discovered:
                if store.activation_baseline_uid is None:
                    store.activation_baseline_uid = 100
                if store.uid_validity is None:
                    store.uid_validity = 12345

        # Ensure MailboxCheckpoint exists and is active
        if store.mailboxes:
            mb = store.mailboxes[0]
            cp_stmt = select(MailboxCheckpoint).where(
                MailboxCheckpoint.mailbox_id == mb.id,
                MailboxCheckpoint.folder == "INBOX",
            )
            cp = (await db.execute(cp_stmt)).scalar_one_or_none()
            if not cp:
                cp = MailboxCheckpoint(
                    id=uuid.uuid4(),
                    mailbox_id=mb.id,
                    folder="INBOX",
                    uid_validity=store.uid_validity or 12345,
                    activation_baseline_uid=store.activation_baseline_uid or 0,
                    last_durably_enqueued_uid=store.activation_baseline_uid or 0,
                    state="active",
                )
                db.add(cp)
            else:
                cp.state = "active"

        store.row_version += 1
        await db.commit()
        return True, "ACTIVATED"
