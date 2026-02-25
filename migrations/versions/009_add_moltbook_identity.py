"""Add MoltBook identity columns to agents.

Revision ID: 009
Revises: 008
Create Date: 2026-02-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("moltbook_id", sa.String(128), nullable=True))
    op.add_column("agents", sa.Column("moltbook_username", sa.String(128), nullable=True))
    op.add_column("agents", sa.Column("moltbook_karma", sa.Integer(), nullable=True))
    op.add_column("agents", sa.Column("moltbook_verified", sa.Boolean(), nullable=False, server_default="false"))
    op.create_unique_constraint("uq_agents_moltbook_id", "agents", ["moltbook_id"])


def downgrade() -> None:
    op.drop_constraint("uq_agents_moltbook_id", "agents", type_="unique")
    op.drop_column("agents", "moltbook_verified")
    op.drop_column("agents", "moltbook_karma")
    op.drop_column("agents", "moltbook_username")
    op.drop_column("agents", "moltbook_id")
