"""Create hosted_agents, agent_secrets, and hosting_usage tables.

Revision ID: 019
Revises: 018
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hosted_agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id"), nullable=False, unique=True),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("container_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("building", "deploying", "running", "sleeping", "stopped", "errored", name="deploymentstatus"),
            nullable=False,
            server_default="building",
        ),
        sa.Column("runtime", sa.String(50), nullable=False),
        sa.Column("region", sa.String(20), nullable=False, server_default="us-west1"),
        sa.Column("build_log", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cpu_limit", sa.String(10), nullable=False, server_default="0.25"),
        sa.Column("memory_limit_mb", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("scale_to_zero", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("idle_timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "agent_secrets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id"), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "key", name="uq_agent_secret_key"),
    )

    op.create_table(
        "hosting_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.agent_id"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("cpu_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_mb_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("agent_id", "period_start", name="uq_hosting_usage_period"),
    )


def downgrade() -> None:
    op.drop_table("hosting_usage")
    op.drop_table("agent_secrets")
    op.drop_table("hosted_agents")
    op.execute("DROP TYPE IF EXISTS deploymentstatus")
