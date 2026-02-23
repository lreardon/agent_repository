"""A2A protocol schema alignment.

- Agent: add a2a_agent_card JSONB
- Listing: rename capability → skill_id
- Job: add a2a_task_id, a2a_context_id
- Review: add role enum, tags array

Revision ID: 006
Revises: 005
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agent: add a2a_agent_card
    op.add_column("agents", sa.Column("a2a_agent_card", JSONB, nullable=True))

    # Listing: rename capability → skill_id
    op.alter_column("listings", "capability", new_column_name="skill_id")
    op.drop_index("ix_listings_capability", table_name="listings")
    op.create_index("ix_listings_skill_id", "listings", ["skill_id"])

    # Job: add a2a fields
    op.add_column("jobs", sa.Column("a2a_task_id", sa.String(256), nullable=True))
    op.add_column("jobs", sa.Column("a2a_context_id", sa.String(256), nullable=True))

    # Review: add role enum and tags
    op.execute("CREATE TYPE reviewrole AS ENUM ('client_reviewing_seller', 'seller_reviewing_client')")
    op.add_column("reviews", sa.Column(
        "role",
        sa.Enum("client_reviewing_seller", "seller_reviewing_client", name="reviewrole", create_type=False),
        nullable=True,
    ))
    op.add_column("reviews", sa.Column("tags", ARRAY(sa.String(64)), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "tags")
    op.drop_column("reviews", "role")
    op.execute("DROP TYPE IF EXISTS reviewrole")

    op.drop_column("jobs", "a2a_context_id")
    op.drop_column("jobs", "a2a_task_id")

    op.drop_index("ix_listings_skill_id", table_name="listings")
    op.alter_column("listings", "skill_id", new_column_name="capability")
    op.create_index("ix_listings_capability", "listings", ["capability"])

    op.drop_column("agents", "a2a_agent_card")
