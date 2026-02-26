"""Review model for post-job ratings."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import ARRAY, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReviewRole(enum.Enum):
    CLIENT_REVIEWING_SELLER = "client_reviewing_seller"
    SELLER_REVIEWING_CLIENT = "seller_reviewing_client"


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_reviews_rating"),
        UniqueConstraint("job_id", "reviewer_agent_id", name="uq_reviews_job_reviewer"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.job_id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    reviewee_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[ReviewRole | None] = mapped_column(
        Enum(ReviewRole, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(64)), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
