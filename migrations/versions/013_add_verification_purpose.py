"""Add purpose column to email_verifications.

Revision ID: 013
Revises: 012
"""

from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"


def upgrade() -> None:
    # Create the enum type first
    purpose_enum = sa.Enum("signup", "recovery", name="verificationpurpose")
    purpose_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "email_verifications",
        sa.Column(
            "purpose",
            purpose_enum,
            nullable=False,
            server_default="signup",
        ),
    )


def downgrade() -> None:
    op.drop_column("email_verifications", "purpose")
    sa.Enum(name="verificationpurpose").drop(op.get_bind(), checkfirst=True)
