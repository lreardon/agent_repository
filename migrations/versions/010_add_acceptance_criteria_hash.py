"""Add acceptance_criteria_hash column to jobs.

Revision ID: 010
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("acceptance_criteria_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "acceptance_criteria_hash")
