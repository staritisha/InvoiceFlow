"""merge_heads

Revision ID: bb9e9419eff2
Revises: 42d6a3590e31, 46ce8e874851
Create Date: 2026-05-26 15:11:13.048892

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb9e9419eff2'
down_revision: Union[str, Sequence[str], None] = ('42d6a3590e31', '46ce8e874851')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
