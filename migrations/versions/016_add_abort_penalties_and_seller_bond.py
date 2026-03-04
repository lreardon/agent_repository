"""add abort penalties and seller bond

Revision ID: 016
Revises: 015_create_deposit_watcher_state
Create Date: 2026-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, None] = '015_create_deposit_watcher_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add abort penalty columns to jobs table
    op.add_column('jobs', sa.Column('client_abort_penalty', sa.Numeric(12, 2), nullable=False, server_default='0.00'))
    op.add_column('jobs', sa.Column('seller_abort_penalty', sa.Numeric(12, 2), nullable=False, server_default='0.00'))

    # Add seller bond amount to escrow_accounts
    op.add_column('escrow_accounts', sa.Column('seller_bond_amount', sa.Numeric(12, 2), nullable=False, server_default='0.00'))

    # Add new enum values to escrowaction type
    # PostgreSQL requires ALTER TYPE ... ADD VALUE for enum extension
    op.execute("ALTER TYPE escrowaction ADD VALUE IF NOT EXISTS 'seller_bond_funded'")
    op.execute("ALTER TYPE escrowaction ADD VALUE IF NOT EXISTS 'abort_client'")
    op.execute("ALTER TYPE escrowaction ADD VALUE IF NOT EXISTS 'abort_seller'")
    op.execute("ALTER TYPE escrowaction ADD VALUE IF NOT EXISTS 'bond_forfeited'")
    op.execute("ALTER TYPE escrowaction ADD VALUE IF NOT EXISTS 'bond_returned'")


def downgrade() -> None:
    op.drop_column('escrow_accounts', 'seller_bond_amount')
    op.drop_column('jobs', 'seller_abort_penalty')
    op.drop_column('jobs', 'client_abort_penalty')
    # Note: PostgreSQL does not support DROP VALUE from enum types.
    # The new escrowaction values will remain but be unused.
