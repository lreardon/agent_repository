"""Pydantic v2 schemas for Reviews."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    tags: list[str] | None = Field(None, max_length=10)
    comment: str | None = Field(None, max_length=4096)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for tag in v:
            if len(tag) > 64:
                raise ValueError("Tag must be <= 64 chars")
        return v


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    review_id: uuid.UUID
    job_id: uuid.UUID
    reviewer_agent_id: uuid.UUID
    reviewee_agent_id: uuid.UUID
    role: str | None = None
    rating: int
    tags: list[str] | None = None
    comment: str | None
    created_at: datetime

    @field_validator("role", mode="before")
    @classmethod
    def serialize_role(cls, v: object) -> str | None:
        if v is None:
            return None
        if hasattr(v, "value"):
            return v.value
        return str(v)
