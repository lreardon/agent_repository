"""SQLAlchemy models for agent hosting."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeploymentStatus(enum.Enum):
    BUILDING = "building"
    DEPLOYING = "deploying"
    RUNNING = "running"
    SLEEPING = "sleeping"  # Image built, pod scaled to zero — wakes on job arrival
    STOPPED = "stopped"
    ERRORED = "errored"


class HostedAgent(Base):
    __tablename__ = "hosted_agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id"), nullable=False, unique=True
    )
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=DeploymentStatus.BUILDING,
    )
    runtime: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str] = mapped_column(String(20), nullable=False, default="us-west1")
    build_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cpu_limit: Mapped[str] = mapped_column(String(10), nullable=False, default="0.25")
    memory_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    scale_to_zero: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    idle_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300  # 5 minutes idle → sleep
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AgentSecret(Base):
    __tablename__ = "agent_secrets"
    __table_args__ = (UniqueConstraint("agent_id", "key", name="uq_agent_secret_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class HostingUsage(Base):
    __tablename__ = "hosting_usage"
    __table_args__ = (
        UniqueConstraint("agent_id", "period_start", name="uq_hosting_usage_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(Date, nullable=False)
    cpu_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_mb_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
