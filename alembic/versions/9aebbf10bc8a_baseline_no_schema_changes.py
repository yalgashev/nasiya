"""baseline: no schema changes

Revision ID: 9aebbf10bc8a
Revises:
Create Date: 2026-07-18 07:48:56.392775

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9aebbf10bc8a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
