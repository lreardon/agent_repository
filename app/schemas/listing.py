"""Pydantic v2 schemas for Listing and Discovery endpoints."""

import re
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CAPABILITY_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")


class ListingCreate(BaseModel):
    skill_id: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(None, max_length=4096)
    price_model: str = Field(..., pattern=r"^(per_call|per_unit|per_hour|flat)$")
    base_price: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    currency: str = Field("credits", max_length=16)
    sla: dict | None = None

    @field_validator("skill_id")
    @classmethod
    def validate_skill_id(cls, v: str) -> str:
        if not _CAPABILITY_PATTERN.match(v):
            raise ValueError("Capability must be alphanumeric + hyphens")
        return v

    @field_validator("base_price")
    @classmethod
    def validate_price(cls, v: Decimal) -> Decimal:
        if v > Decimal("1000000"):
            raise ValueError("Maximum price is 1,000,000 credits")
        return v


class ListingUpdate(BaseModel):
    description: str | None = Field(None, max_length=4096)
    price_model: str | None = Field(None, pattern=r"^(per_call|per_unit|per_hour|flat)$")
    base_price: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    sla: dict | None = None
    status: str | None = Field(None, pattern=r"^(active|paused|archived)$")

    @field_validator("base_price")
    @classmethod
    def validate_price(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v > Decimal("1000000"):
            raise ValueError("Maximum price is 1,000,000 credits")
        return v


class ListingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    listing_id: uuid.UUID
    seller_agent_id: uuid.UUID
    skill_id: str
    description: str | None
    price_model: str
    base_price: Decimal
    currency: str
    sla: dict | None
    status: str
    created_at: datetime

    @field_validator("price_model", "status", mode="before")
    @classmethod
    def serialize_enum(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)


class DiscoverQuery(BaseModel):
    """Query params for GET /discover."""
    skill_id: str | None = None
    min_rating: Decimal | None = Field(None, ge=0, le=5)
    max_price: Decimal | None = Field(None, gt=0)
    price_model: str | None = Field(None, pattern=r"^(per_call|per_unit|per_hour|flat)$")
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class A2ASkillInfo(BaseModel):
    """A2A skill metadata from the agent's Agent Card."""
    name: str | None = None
    description: str | None = None
    tags: list[str] = []
    examples: list[str] = []


class DiscoverResult(BaseModel):
    """A listing with seller reputation and A2A skill metadata."""
    model_config = ConfigDict(from_attributes=True)

    listing_id: uuid.UUID
    seller_agent_id: uuid.UUID
    seller_display_name: str
    seller_reputation: Decimal
    skill_id: str
    description: str | None
    price_model: str
    base_price: Decimal
    currency: str
    sla: dict | None
    a2a_skill: A2ASkillInfo | None = None
