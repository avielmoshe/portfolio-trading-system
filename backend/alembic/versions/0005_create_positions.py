"""create positions table

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("total_quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("average_buy_price", sa.Numeric(18, 8), nullable=False),
        sa.UniqueConstraint("user_id", "symbol", name="uq_positions_user_symbol"),
        sa.CheckConstraint("total_quantity >= 0", name="ck_positions_quantity_non_negative"),
        sa.CheckConstraint("average_buy_price >= 0", name="ck_positions_avg_price_non_negative"),
    )


def downgrade() -> None:
    op.drop_table("positions")
