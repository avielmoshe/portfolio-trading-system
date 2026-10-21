import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CandleInterval(str, enum.Enum):
    """Aggregation period of one candle. This table holds AGGREGATED candles only — never raw
    tick data. Live ticks belong in Redis (see realtime_expert.md); storing every tick here
    would grow without bound and make chart range queries scan millions of rows per request."""

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


# values_callable: store the wire values ("1m"), not the Python member names ("ONE_MINUTE"),
# so the DB enum matches what the API and the chart library already speak.
INTERVAL_ENUM = Enum(
    CandleInterval,
    name="candle_interval",
    native_enum=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class HistoricalPrice(Base):
    """One aggregated OHLCV candle for one asset at one interval. `timestamp` is the candle's
    OPEN time (the start of the period), which is what charting libraries expect."""

    __tablename__ = "historical_prices"
    __table_args__ = (
        # Makes re-importing a range idempotent: the same candle upserts instead of duplicating.
        # Without it a retried import would double every bar on the chart.
        UniqueConstraint("asset_id", "interval", "timestamp", name="uq_historical_prices_asset_interval_ts"),
        # OHLC sanity, enforced by the DB: a bad feed or a unit mix-up is rejected at write time
        # rather than discovered later as an impossible candle on a customer's chart.
        CheckConstraint("high >= low", name="ck_historical_prices_high_ge_low"),
        CheckConstraint("high >= open AND high >= close", name="ck_historical_prices_high_is_max"),
        CheckConstraint("low <= open AND low <= close", name="ck_historical_prices_low_is_min"),
        CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="ck_historical_prices_positive"),
        CheckConstraint("volume >= 0", name="ck_historical_prices_volume_non_negative"),
        # Exactly the chart query's shape: one asset, one interval, a time range read newest-first.
        # DESC on timestamp so "the latest candle" is a single index step, not a sort.
        Index(
            "ix_historical_prices_asset_interval_ts",
            "asset_id",
            "interval",
            "timestamp",
            postgresql_ops={"timestamp": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    interval: Mapped[CandleInterval] = mapped_column(INTERVAL_ENUM)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Numeric, like every other price in the schema: a float close would disagree with the
    # Numeric(18,8) average_buy_price it gets compared against when computing P&L.
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    # BigInteger: daily share volume for a liquid symbol overflows a 32-bit int.
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
