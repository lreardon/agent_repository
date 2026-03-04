"""Pydantic v2 schemas for webhook delivery endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    created_at: datetime
    last_error: str | None

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)
