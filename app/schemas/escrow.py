"""Pydantic v2 schemas for Escrow."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class EscrowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    escrow_id: uuid.UUID
    job_id: uuid.UUID
    client_agent_id: uuid.UUID
    seller_agent_id: uuid.UUID
    amount: Decimal
    status: str
    funded_at: datetime | None
    released_at: datetime | None

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)
