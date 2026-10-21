"""create users table

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The dev DB may already have this table from the old create_all() bootstrap
    # (with a Float balance). Skipping creation lets `upgrade head` work on both
    # fresh and existing databases; 0002 then fixes the column type.
    if sa.inspect(op.get_bind()).has_table("users"):
        return
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("cash_balance", sa.Float(), nullable=True, server_default="10000"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_table("users")
