"""Job SQLAlchemy model — full lifecycle entity."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class JobStatus(enum.Enum):
    PROPOSED = "proposed"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    FUNDED = "funded"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    DISPUTED = "disputed"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


# Valid state transitions
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PROPOSED: {JobStatus.NEGOTIATING, JobStatus.AGREED, JobStatus.CANCELLED},
    JobStatus.NEGOTIATING: {JobStatus.AGREED, JobStatus.CANCELLED},
    JobStatus.AGREED: {JobStatus.FUNDED, JobStatus.CANCELLED},
    JobStatus.FUNDED: {JobStatus.IN_PROGRESS},
    JobStatus.IN_PROGRESS: {JobStatus.DELIVERED, JobStatus.FAILED},
    JobStatus.DELIVERED: {JobStatus.VERIFYING, JobStatus.FAILED},
    JobStatus.VERIFYING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.DISPUTED},
    JobStatus.DISPUTED: {JobStatus.RESOLVED},
    JobStatus.RESOLVED: set(),
    JobStatus.CANCELLED: set(),
}


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    client_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    seller_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("listings.listing_id", ondelete="RESTRICT"), nullable=True
    )
    a2a_task_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    a2a_context_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=JobStatus.PROPOSED,
    )
    acceptance_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    acceptance_criteria_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requirements: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    agreed_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    negotiation_log: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    client = relationship("Agent", foreign_keys=[client_agent_id], lazy="selectin")
    seller = relationship("Agent", foreign_keys=[seller_agent_id], lazy="selectin")
