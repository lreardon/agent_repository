"""Create reviews and webhook_deliveries tables.

Revision ID: 005
Revises: 004
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("review_id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.job_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewee_agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_reviews_rating"),
    )
    op.create_index("ix_reviews_reviewee_agent_id", "reviews", ["reviewee_agent_id"])
    op.create_index("ix_reviews_job_id", "reviews", ["job_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.Uuid(), primary_key=True),
        sa.Column("target_agent_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "delivered", "failed", name="webhookstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_deliveries_target_agent_id", "webhook_deliveries", ["target_agent_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("reviews")
    op.execute("DROP TYPE IF EXISTS webhookstatus")
