"""Create escrow_accounts and escrow_audit_log tables.

Revision ID: 004
Revises: 003
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "escrow_accounts",
        sa.Column("escrow_id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.job_id", ondelete="RESTRICT"), unique=True, nullable=False),
        sa.Column("client_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("seller_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "funded", "released", "refunded", "disputed", name="escrowstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "escrow_audit_log",
        sa.Column("escrow_audit_id", sa.Uuid(), primary_key=True),
        sa.Column("escrow_id", sa.Uuid(), sa.ForeignKey("escrow_accounts.escrow_id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "action",
            sa.Enum("created", "funded", "released", "refunded", "disputed", "resolved", name="escrowaction"),
            nullable=False,
        ),
        sa.Column("actor_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata", JSONB, nullable=True),
    )
    op.create_index("ix_escrow_audit_log_escrow_id", "escrow_audit_log", ["escrow_id"])


def downgrade() -> None:
    op.drop_table("escrow_audit_log")
    op.drop_table("escrow_accounts")
    op.execute("DROP TYPE IF EXISTS escrowstatus")
    op.execute("DROP TYPE IF EXISTS escrowaction")
