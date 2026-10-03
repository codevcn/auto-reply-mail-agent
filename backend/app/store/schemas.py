"""Pydantic schemas for StoreProfile management and Setup Wizard."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class StoreProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    brand_name: str = Field(..., min_length=1, max_length=100)
    public_domain: str = Field(..., min_length=1, max_length=255)
    canonical_domain: str = Field(..., min_length=1, max_length=255)
    mailbox_address: str = Field(..., min_length=3, max_length=255)
    mailbox_password: str | None = None
    brand_voice: str | None = None
    email_signature: str | None = None
    brand_description: str | None = None
    default_language: str = "en"
    proxy_profile_id: str | None = None
    shopify_client_id: str | None = None
    shopify_client_secret: str | None = None


class StoreProfileUpdate(BaseModel):
    name: str | None = None
    brand_name: str | None = None
    public_domain: str | None = None
    canonical_domain: str | None = None
    mailbox_address: str | None = None
    mailbox_password: str | None = None
    brand_voice: str | None = None
    email_signature: str | None = None
    brand_description: str | None = None
    default_language: str | None = None
    proxy_profile_id: str | None = None
    shopify_client_id: str | None = None
    shopify_client_secret: str | None = None
    status: str | None = None


class StoreProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    brand_name: str
    public_domain: str
    canonical_domain: str | None = None
    industry: str | None = None
    brand_description: str | None = None
    default_language: str
    tone_of_voice: str | None = None
    email_signature: str | None = None
    status: str
    proxy_profile_id: uuid.UUID | None = None
    activation_baseline_uid: int | None = None
    uid_validity: int | None = None
    has_mailbox: bool = False
    has_shopify: bool = False
    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


class StoreActivateRequest(BaseModel):
    mailbox_tested: bool = False
    proxy_tested: bool = False
    shopify_tested: bool = False


class StoreActivateResponse(BaseModel):
    success: bool
    status: str
    message: str
    store_id: uuid.UUID
