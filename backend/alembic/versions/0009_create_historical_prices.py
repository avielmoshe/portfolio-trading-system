"""create historical_prices (aggregated OHLCV candles)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

interval_enum = sa.Enum("1m", "5m", "15m", "1h", "1d", name="candle_interval")


def upgrade() -> None:
    # create_table emits CREATE TYPE for the Enum column itself; creating it here too would
    # raise DuplicateObject. drop_table does not drop the type, so downgrade() removes it.
    op.create_table(
        "historical_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interval", interval_enum, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 8), nullable=False),
        sa.Column("high", sa.Numeric(18, 8), nullable=False),
        sa.Column("low", sa.Numeric(18, 8), nullable=False),
        sa.Column("close", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("asset_id", "interval", "timestamp", name="uq_historical_prices_asset_interval_ts"),
        sa.CheckConstraint("high >= low", name="ck_historical_prices_high_ge_low"),
        sa.CheckConstraint("high >= open AND high >= close", name="ck_historical_prices_high_is_max"),
        sa.CheckConstraint("low <= open AND low <= close", name="ck_historical_prices_low_is_min"),
        sa.CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="ck_historical_prices_positive"),
        sa.CheckConstraint("volume >= 0", name="ck_historical_prices_volume_non_negative"),
    )
    op.create_index("ix_historical_prices_asset_id", "historical_prices", ["asset_id"])
    op.execute(
        "CREATE INDEX ix_historical_prices_asset_interval_ts"
        " ON historical_prices (asset_id, interval, timestamp DESC)"
    )


def downgrade() -> None:
    op.drop_table("historical_prices")
    interval_enum.drop(op.get_bind(), checkfirst=True)
