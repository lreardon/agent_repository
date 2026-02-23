"""Escrow account and audit log models."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EscrowStatus(enum.Enum):
    PENDING = "pending"
    FUNDED = "funded"
    RELEASED = "released"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class EscrowAction(enum.Enum):
    CREATED = "created"
    FUNDED = "funded"
    RELEASED = "released"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    RESOLVED = "resolved"


class EscrowAccount(Base):
    __tablename__ = "escrow_accounts"

    escrow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.job_id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    client_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    seller_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[EscrowStatus] = mapped_column(
        Enum(EscrowStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=EscrowStatus.PENDING,
    )
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EscrowAuditLog(Base):
    """Append-only audit log. Never update or delete rows."""
    __tablename__ = "escrow_audit_log"

    escrow_audit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    escrow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("escrow_accounts.escrow_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[EscrowAction] = mapped_column(
        Enum(EscrowAction, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
