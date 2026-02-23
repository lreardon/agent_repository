"""Unique constraint: one active listing per seller per skill.

Revision ID: 007
Revises: 006
"""

from alembic import op

revision = "007"
down_revision = "006"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_listing_seller_skill_active",
        "listings",
        ["seller_agent_id", "skill_id", "status"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_listing_seller_skill_active", "listings", type_="unique")
