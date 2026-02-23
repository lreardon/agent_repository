"""Create jobs table.

Revision ID: 003
Revises: 002
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("client_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("seller_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("listing_id", sa.Uuid(), sa.ForeignKey("listings.listing_id", ondelete="RESTRICT"), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "proposed", "negotiating", "agreed", "funded", "in_progress",
                "delivered", "verifying", "completed", "failed", "disputed",
                "resolved", "cancelled",
                name="jobstatus",
            ),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column("acceptance_criteria", JSONB, nullable=True),
        sa.Column("requirements", JSONB, nullable=True),
        sa.Column("agreed_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("delivery_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("negotiation_log", JSONB, nullable=True, server_default="[]"),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_client_agent_id", "jobs", ["client_agent_id"])
    op.create_index("ix_jobs_seller_agent_id", "jobs", ["seller_agent_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.execute("DROP TYPE IF EXISTS jobstatus")
