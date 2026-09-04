"""Add chart_theme to chats

Revision ID: b3f1a27c94d5
Revises: 6977271c47c1
Create Date: 2026-08-28 10:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f1a27c94d5'
down_revision: Union[str, Sequence[str], None] = '6977271c47c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default backfills existing rows; the column stays NOT NULL.
    op.add_column(
        'chats',
        sa.Column('chart_theme', sa.String(length=16), nullable=False,
                  server_default='light'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('chats', 'chart_theme')
