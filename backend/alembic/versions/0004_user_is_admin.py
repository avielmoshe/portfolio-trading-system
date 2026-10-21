"""users.is_admin

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing users become non-admin via the server default; promote one by hand with SQL.
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "is_admin")
