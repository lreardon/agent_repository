"""Create listings table.

Revision ID: 002
Revises: 001
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("listing_id", sa.Uuid(), primary_key=True),
        sa.Column("seller_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "price_model",
            sa.Enum("per_call", "per_unit", "per_hour", "flat", name="pricemodel"),
            nullable=False,
        ),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False, server_default="credits"),
        sa.Column("sla", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "paused", "archived", name="listingstatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_listings_capability", "listings", ["capability"])
    op.create_index("ix_listings_seller_agent_id", "listings", ["seller_agent_id"])
    op.create_index("ix_listings_status", "listings", ["status"])


def downgrade() -> None:
    op.drop_table("listings")
    op.execute("DROP TYPE IF EXISTS pricemodel")
    op.execute("DROP TYPE IF EXISTS listingstatus")
