"""add dashboard_token to accounts

Revision ID: 017
Revises: 016
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("dashboard_token", sa.String(128), unique=True, nullable=True))
    op.add_column("accounts", sa.Column("dashboard_token_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "dashboard_token_expires_at")
    op.drop_column("accounts", "dashboard_token")
