"""positions: symbol string -> asset_id FK

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable first: existing rows have no asset_id yet, and NOT NULL would reject them.
    op.add_column("positions", sa.Column("asset_id", sa.Integer(), nullable=True))

    # Any symbol held but missing from the catalogue gets a placeholder asset row. Without this
    # the join below would leave asset_id NULL and the migration would fail at SET NOT NULL —
    # losing a real holding is never acceptable, so we create the asset instead of dropping data.
    op.execute(
        """
        INSERT INTO assets (symbol, company_name, is_active)
        SELECT DISTINCT p.symbol, p.symbol, false
        FROM positions p
        LEFT JOIN assets a ON a.symbol = p.symbol
        WHERE a.id IS NULL
        """
    )
    op.execute("UPDATE positions p SET asset_id = a.id FROM assets a WHERE a.symbol = p.symbol")

    op.alter_column("positions", "asset_id", nullable=False)
    op.create_foreign_key(
        "positions_asset_id_fkey", "positions", "assets", ["asset_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_positions_asset_id", "positions", ["asset_id"])

    # Swap the uniqueness guarantee only after asset_id is populated, so the table is never
    # momentarily free of a one-row-per-user-per-instrument rule.
    op.drop_constraint("uq_positions_user_symbol", "positions", type_="unique")
    op.create_unique_constraint("uq_positions_user_asset", "positions", ["user_id", "asset_id"])
    op.drop_column("positions", "symbol")


def downgrade() -> None:
    op.add_column("positions", sa.Column("symbol", sa.String(16), nullable=True))
    op.execute("UPDATE positions p SET symbol = a.symbol FROM assets a WHERE a.id = p.asset_id")
    op.alter_column("positions", "symbol", nullable=False)
    op.drop_constraint("uq_positions_user_asset", "positions", type_="unique")
    op.create_unique_constraint("uq_positions_user_symbol", "positions", ["user_id", "symbol"])
    op.drop_index("ix_positions_asset_id", table_name="positions")
    op.drop_constraint("positions_asset_id_fkey", "positions", type_="foreignkey")
    op.drop_column("positions", "asset_id")
