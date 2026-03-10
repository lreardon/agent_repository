"""Pydantic v2 schemas for Agent Hosting endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeployRequest(BaseModel):
    """Upload agent code for hosted deployment."""

    runtime: str = Field(
        default="python:3.13",
        description="Runtime environment",
    )
    cpu_limit: str = Field(
        default="0.25",
        description="CPU limit (vCPU cores)",
    )
    memory_limit_mb: int = Field(
        default=512,
        ge=128,
        le=4096,
        description="Memory limit in MB",
    )
    region: str = Field(
        default="us-west1",
        description="Deployment region",
    )
    env_vars: dict[str, str] | None = Field(
        None,
        description="Environment variables (non-secret)",
    )

    @field_validator("runtime")
    @classmethod
    def validate_runtime(cls, v: str) -> str:
        allowed = {"python:3.13", "python:3.12", "node:20", "node:22"}
        if v not in allowed:
            raise ValueError(f"runtime must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("cpu_limit")
    @classmethod
    def validate_cpu(cls, v: str) -> str:
        allowed = {"0.25", "0.5", "1", "2"}
        if v not in allowed:
            raise ValueError(f"cpu_limit must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("region")
    @classmethod
    def validate_region(cls, v: str) -> str:
        allowed = {"us-west1", "us-east1", "us-central1", "europe-west1"}
        if v not in allowed:
            raise ValueError(f"region must be one of: {', '.join(sorted(allowed))}")
        return v


class DeployResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    status: str
    runtime: str
    region: str
    cpu_limit: str
    memory_limit_mb: int
    source_hash: str
    container_id: str | None = None
    build_log: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)


class DeployStatusResponse(BaseModel):
    status: str
    container_id: str | None = None
    build_log: str | None = None
    error_message: str | None = None
    updated_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def serialize_status(cls, v: object) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)


class SecretCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Z_][A-Z0-9_]*$")
    value: str = Field(..., min_length=1, max_length=8192)


class SecretResponse(BaseModel):
    key: str
    created_at: datetime


class SecretsListResponse(BaseModel):
    secrets: list[SecretResponse]


class LogsResponse(BaseModel):
    logs: str
    container_id: str | None = None


class UsageResponse(BaseModel):
    agent_id: uuid.UUID
    period_start: str
    cpu_seconds: int
    memory_mb_seconds: int
    requests_count: int
