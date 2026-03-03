"""Make endpoint_url optional, add presence and hosting_mode columns.

Revision ID: 014
Revises: 013
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"


def upgrade() -> None:
    # Make endpoint_url nullable
    op.alter_column("agents", "endpoint_url", existing_type=sa.String(2048), nullable=True)

    # Add hosting_mode column
    op.add_column(
        "agents",
        sa.Column(
            "hosting_mode",
            sa.String(20),
            nullable=False,
            server_default="external",
        ),
    )

    # Add presence columns
    op.add_column(
        "agents",
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "agents",
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "last_connected_at")
    op.drop_column("agents", "is_online")
    op.drop_column("agents", "hosting_mode")
    op.alter_column("agents", "endpoint_url", existing_type=sa.String(2048), nullable=False)
