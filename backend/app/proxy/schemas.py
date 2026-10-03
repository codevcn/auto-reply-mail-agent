"""Pydantic schemas for Proxy management and diagnostics."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ProxyProfileBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    protocol: Literal["socks5", "http"] = "socks5"
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    connect_timeout_seconds: int = Field(default=10, ge=1, le=120)
    enabled: bool = True


class ProxyProfileCreate(ProxyProfileBase):
    pass


class ProxyProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    protocol: Literal["socks5", "http"] | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    connect_timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    enabled: bool | None = None


class ProxyProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    protocol: str
    host: str
    port: int
    username: str | None = None
    has_password: bool
    connect_timeout_seconds: int
    enabled: bool
    last_test_status: str | None = None
    last_tested_at: datetime.datetime | None = None
    last_exit_ip: str | None = None
    last_detected_country: str | None = None
    last_latency_ms: int | None = None
    last_error_code: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


class ProxyCandidateTestRequest(BaseModel):
    protocol: Literal["socks5", "http"] = "socks5"
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    connect_timeout_seconds: int = Field(default=10, ge=1, le=120)
    remote_dns_enabled: bool = True


class ProxyTestResult(BaseModel):
    success: bool
    exit_ip: str | None = None
    country: str | None = None
    latency_ms: int | None = None
    remote_dns_active: bool = True
    error: str | None = None
    detail: str | None = None
