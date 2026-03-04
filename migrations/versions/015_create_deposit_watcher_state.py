"""create deposit_watcher_state table

Revision ID: 015_create_deposit_watcher_state
Revises: 014_optional_endpoint_and_presence
Create Date: 2026-03-03 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '015_create_deposit_watcher_state'
down_revision: Union[str, None] = '014_optional_endpoint_and_presence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = '014_optional_endpoint_and_presence'


def upgrade() -> None:
    """Create deposit_watcher_state table."""
    op.create_table(
        'deposit_watcher_state',
        sa.Column('id', sa.Integer(), primary_key=True, default=1),
        sa.Column('last_scanned_block', sa.Integer(), nullable=False, default=0),
    )


def downgrade() -> None:
    """Drop deposit_watcher_state table."""
    op.drop_table('deposit_watcher_state')
