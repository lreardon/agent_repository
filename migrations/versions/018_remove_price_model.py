"""Remove price_model column — all pricing is per-job (flat).

Revision ID: 018
Revises: 017
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("listings", "price_model")
    op.execute("DROP TYPE IF EXISTS pricemodel")


def downgrade() -> None:
    # Recreate the enum and column with default 'flat'
    op.execute("CREATE TYPE pricemodel AS ENUM ('per_call', 'per_unit', 'per_hour', 'flat')")
    op.add_column(
        "listings",
        sa.Column(
            "price_model",
            sa.Enum("per_call", "per_unit", "per_hour", "flat", name="pricemodel"),
            nullable=False,
            server_default="flat",
        ),
    )
