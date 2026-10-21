"""cash_balance: Float -> Numeric(18,8) + non-negative check

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_users_cash_balance_non_negative"


def upgrade() -> None:
    # Old rows could hold NULL (the column was nullable); NOT NULL below would fail on them.
    op.execute("UPDATE users SET cash_balance = 10000 WHERE cash_balance IS NULL")
    # USING tells Postgres how to convert existing rows; ALTER TYPE rewrites the
    # column inside this migration's transaction, so it's all-or-nothing.
    op.alter_column(
        "users",
        "cash_balance",
        type_=sa.Numeric(18, 8),
        existing_type=sa.Float(),
        postgresql_using="cash_balance::numeric(18,8)",
        existing_nullable=True,
        nullable=False,
        server_default=None,
    )
    op.create_check_constraint(CONSTRAINT, "users", "cash_balance >= 0")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "users", type_="check")
    op.alter_column(
        "users",
        "cash_balance",
        type_=sa.Float(),
        existing_type=sa.Numeric(18, 8),
        postgresql_using="cash_balance::double precision",
        nullable=True,
    )
