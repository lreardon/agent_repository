"""Add unique constraint: one review per reviewer per job.

Revision ID: 011
"""

from alembic import op

revision = "011"
down_revision = "010"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_reviews_job_reviewer",
        "reviews",
        ["job_id", "reviewer_agent_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_reviews_job_reviewer", "reviews", type_="unique")
