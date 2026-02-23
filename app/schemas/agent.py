"""Pydantic v2 schemas for Agent endpoints."""

import ipaddress
import re
import uuid
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CAPABILITY_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")

# Private/internal IP ranges for SSRF protection
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_endpoint_url(url: str) -> str:
    """Validate endpoint URL: must be HTTPS, no private IPs."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("endpoint_url must use HTTPS")
    if not parsed.hostname:
        raise ValueError("endpoint_url must have a valid hostname")

    # Check for IP-based URLs against blocked ranges
    try:
        addr = ipaddress.ip_address(parsed.hostname)
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                raise ValueError("endpoint_url must not point to a private/internal IP")
    except ValueError as e:
        if "private/internal" in str(e):
            raise
        # hostname is not an IP — that's fine, it's a domain
        pass

    return url


class AgentCreate(BaseModel):
    public_key: str = Field(..., max_length=128, description="Ed25519 public key (hex)")
    display_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=4096)
    endpoint_url: str = Field(..., max_length=2048)
    capabilities: list[str] | None = Field(None, max_length=20)

    @field_validator("endpoint_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_endpoint_url(v)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 20:
            raise ValueError("Maximum 20 capabilities allowed")
        for cap in v:
            if len(cap) > 64:
                raise ValueError(f"Capability tag must be <= 64 chars: {cap}")
            if not _CAPABILITY_PATTERN.match(cap):
                raise ValueError(f"Capability must be alphanumeric + hyphens: {cap}")
        return v


class AgentUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=4096)
    endpoint_url: str | None = Field(None, max_length=2048)
    capabilities: list[str] | None = Field(None, max_length=20)

    @field_validator("endpoint_url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_endpoint_url(v)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 20:
            raise ValueError("Maximum 20 capabilities allowed")
        for cap in v:
            if len(cap) > 64:
                raise ValueError(f"Capability tag must be <= 64 chars: {cap}")
            if not _CAPABILITY_PATTERN.match(cap):
                raise ValueError(f"Capability must be alphanumeric + hyphens: {cap}")
        return v


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    public_key: str
    display_name: str
    description: str | None
    endpoint_url: str
    capabilities: list[str] | None
    reputation_seller: Decimal
    reputation_client: Decimal
    a2a_agent_card: dict | None = None
    status: str

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)
    created_at: datetime
    last_seen: datetime


class BalanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    balance: Decimal


class ReputationResponse(BaseModel):
    agent_id: uuid.UUID
    reputation_seller: Decimal | None = None
    reputation_seller_display: str  # numeric or "New"
    reputation_client: Decimal | None = None
    reputation_client_display: str
    total_reviews_as_seller: int
    total_reviews_as_client: int
    top_tags: list[str] = []


class DepositRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v > Decimal("1000000"):
            raise ValueError("Maximum deposit is 1,000,000 credits")
        return v
