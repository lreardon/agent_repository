"""Create wallet tables: deposit_addresses, deposit_transactions, withdrawal_requests.

Revision ID: 008
Revises: 007
Create Date: 2026-02-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deposit_addresses",
        sa.Column("deposit_address_id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), unique=True, nullable=False),
        sa.Column("address", sa.String(42), unique=True, nullable=False),
        sa.Column("derivation_index", sa.Integer(), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "deposit_transactions",
        sa.Column("deposit_tx_id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tx_hash", sa.String(66), unique=True, nullable=False),
        sa.Column("from_address", sa.String(42), nullable=False),
        sa.Column("amount_usdc", sa.Numeric(18, 6), nullable=False),
        sa.Column("amount_credits", sa.Numeric(12, 2), nullable=False),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum("pending", "confirming", "credited", "failed", name="depositstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_deposit_transactions_agent_id", "deposit_transactions", ["agent_id"])
    op.create_index("ix_deposit_transactions_status", "deposit_transactions", ["status"])

    op.create_table(
        "withdrawal_requests",
        sa.Column("withdrawal_id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("fee", sa.Numeric(12, 2), nullable=False),
        sa.Column("net_payout", sa.Numeric(12, 2), nullable=False),
        sa.Column("destination_address", sa.String(42), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", name="withdrawalstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("tx_hash", sa.String(66), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_withdrawal_requests_agent_id", "withdrawal_requests", ["agent_id"])
    op.create_index("ix_withdrawal_requests_status", "withdrawal_requests", ["status"])


def downgrade() -> None:
    op.drop_table("withdrawal_requests")
    op.drop_table("deposit_transactions")
    op.drop_table("deposit_addresses")
    op.execute("DROP TYPE IF EXISTS withdrawalstatus")
    op.execute("DROP TYPE IF EXISTS depositstatus")
