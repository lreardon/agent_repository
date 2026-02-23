"""Service Listing SQLAlchemy model."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PriceModel(enum.Enum):
    PER_CALL = "per_call"
    PER_UNIT = "per_unit"
    PER_HOUR = "per_hour"
    FLAT = "flat"


class ListingStatus(enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint(
            "seller_agent_id", "skill_id", "status",
            name="uq_listing_seller_skill_active",
        ),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    seller_agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False
    )
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_model: Mapped[PriceModel] = mapped_column(
        Enum(PriceModel, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="credits")
    sla: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ListingStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    seller = relationship("Agent", backref="listings", lazy="selectin")
