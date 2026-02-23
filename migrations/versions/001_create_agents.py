"""Create agents table.

Revision ID: 001
Revises: None
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("agent_id", sa.Uuid(), primary_key=True),
        sa.Column("public_key", sa.String(128), unique=True, nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("endpoint_url", sa.String(2048), nullable=False),
        sa.Column("capabilities", sa.ARRAY(sa.String(64)), nullable=True),
        sa.Column("webhook_secret", sa.String(64), nullable=False),
        sa.Column("reputation_seller", sa.Numeric(3, 2), nullable=False, server_default="0.00"),
        sa.Column("reputation_client", sa.Numeric(3, 2), nullable=False, server_default="0.00"),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column(
            "status",
            sa.Enum("active", "suspended", "deactivated", name="agentstatus"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agents")
    op.execute("DROP TYPE IF EXISTS agentstatus")
