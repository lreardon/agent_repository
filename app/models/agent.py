"""Agent SQLAlchemy model."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import ARRAY, DateTime, Enum, Numeric, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentStatus(enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    public_key: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    capabilities: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(64)), nullable=True, default=list
    )
    webhook_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    reputation_seller: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0.00")
    )
    reputation_client: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0.00")
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=AgentStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    a2a_agent_card: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
