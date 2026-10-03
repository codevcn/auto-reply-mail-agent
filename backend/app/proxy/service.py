"""Business service for proxy management and health tests."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models.store import ProxyProfile
from app.proxy.client import ProxyHealthChecker, ResolvedProxyConfig
from app.proxy.schemas import (
    ProxyCandidateTestRequest,
    ProxyProfileCreate,
    ProxyProfileUpdate,
    ProxyTestResult,
)


class ProxyService:
    """Service to handle proxy profile persistence and diagnostics."""

    @staticmethod
    async def create_proxy_profile(db: AsyncSession, data: ProxyProfileCreate) -> ProxyProfile:
        encrypted_username = encrypt_secret(data.username) if data.username else None
        encrypted_password = encrypt_secret(data.password) if data.password else None

        profile = ProxyProfile(
            name=data.name,
            protocol=data.protocol,
            host=data.host,
            port=data.port,
            encrypted_username=encrypted_username,
            encrypted_password=encrypted_password,
            connect_timeout_seconds=data.connect_timeout_seconds,
            enabled=data.enabled,
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        return profile

    @staticmethod
    async def get_proxy_profile(db: AsyncSession, proxy_id: uuid.UUID) -> ProxyProfile | None:
        stmt = select(ProxyProfile).where(ProxyProfile.id == proxy_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_proxy_profile_by_name(db: AsyncSession, name: str) -> ProxyProfile | None:
        stmt = select(ProxyProfile).where(ProxyProfile.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_proxy_profiles(db: AsyncSession) -> list[ProxyProfile]:
        stmt = select(ProxyProfile).order_by(ProxyProfile.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_proxy_profile(
        db: AsyncSession, proxy_id: uuid.UUID, data: ProxyProfileUpdate
    ) -> ProxyProfile | None:
        profile = await ProxyService.get_proxy_profile(db, proxy_id)
        if not profile:
            return None

        if data.name is not None:
            profile.name = data.name
        if data.protocol is not None:
            profile.protocol = data.protocol
        if data.host is not None:
            profile.host = data.host
        if data.port is not None:
            profile.port = data.port
        if data.username is not None:
            profile.encrypted_username = encrypt_secret(data.username) if data.username else None
        if data.password is not None and data.password != "":
            # Only update password if non-empty string is provided
            profile.encrypted_password = encrypt_secret(data.password)
        if data.connect_timeout_seconds is not None:
            profile.connect_timeout_seconds = data.connect_timeout_seconds
        if data.enabled is not None:
            profile.enabled = data.enabled

        profile.row_version += 1
        await db.commit()
        await db.refresh(profile)
        return profile

    @staticmethod
    async def delete_proxy_profile(db: AsyncSession, proxy_id: uuid.UUID) -> bool:
        profile = await ProxyService.get_proxy_profile(db, proxy_id)
        if not profile:
            return False

        await db.delete(profile)
        await db.commit()
        return True

    @staticmethod
    def resolve_proxy_config(profile: ProxyProfile) -> ResolvedProxyConfig:
        username = decrypt_secret(profile.encrypted_username) if profile.encrypted_username else None
        password = decrypt_secret(profile.encrypted_password) if profile.encrypted_password else None
        return ResolvedProxyConfig(
            id=str(profile.id),
            host=profile.host,
            port=profile.port,
            username=username,
            password=password,
            protocol=profile.protocol,
            connect_timeout=profile.connect_timeout_seconds,
        )

    @staticmethod
    async def test_candidate_proxy(data: ProxyCandidateTestRequest) -> ProxyTestResult:
        config = ResolvedProxyConfig(
            host=data.host,
            port=data.port,
            username=data.username,
            password=data.password,
            protocol=data.protocol,
            connect_timeout=data.connect_timeout_seconds,
        )
        return await ProxyHealthChecker.test_proxy(
            config, remote_dns_enabled=data.remote_dns_enabled
        )

    @staticmethod
    async def test_stored_proxy(db: AsyncSession, proxy_id: uuid.UUID) -> ProxyTestResult:
        profile = await ProxyService.get_proxy_profile(db, proxy_id)
        if not profile:
            return ProxyTestResult(
                success=False,
                error="PROXY_NOT_FOUND",
                detail=f"Proxy with ID {proxy_id} does not exist.",
            )

        config = ProxyService.resolve_proxy_config(profile)
        result = await ProxyHealthChecker.test_proxy(config, remote_dns_enabled=True)

        # Update diagnostic cache on model
        profile.last_test_status = "passed" if result.success else "failed"
        profile.last_tested_at = datetime.datetime.now(datetime.UTC)
        profile.last_exit_ip = result.exit_ip
        profile.last_detected_country = result.country
        profile.last_latency_ms = result.latency_ms
        profile.last_error_code = result.error
        await db.commit()

        return result
