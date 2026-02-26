"""Create accounts and email_verifications tables.

Revision ID: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "012"
down_revision = "011"


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("account_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.agent_id", ondelete="SET NULL"),
            unique=True,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"])

    op.create_table(
        "email_verifications",
        sa.Column("verification_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("token", sa.String(128), unique=True, nullable=False),
        sa.Column("registration_token", sa.String(128), unique=True, nullable=True),
        sa.Column("registration_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_email_verifications_token", "email_verifications", ["token"])
    op.create_index(
        "ix_email_verifications_registration_token",
        "email_verifications",
        ["registration_token"],
    )


def downgrade() -> None:
    op.drop_table("email_verifications")
    op.drop_table("accounts")
