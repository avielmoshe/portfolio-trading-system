from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.historical_price import CandleInterval, HistoricalPrice


class Candle(TypedDict):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


# Aggregated candles only. Raw ticks live in Redis with a TTL; persisting every tick here would
# grow without bound and turn a chart range query into a scan of millions of rows.


def upsert_candles(
    db: Session,
    asset_id: int,
    interval: CandleInterval,
    candles: Sequence[Candle],
) -> int:
    """Insert or refresh a batch of candles. Returns the number of rows sent.

    One multi-row statement, not a loop: a 1000-candle import becomes a single round trip.
    ON CONFLICT DO UPDATE makes re-importing a range idempotent — the in-progress candle gets
    corrected as it closes, instead of the import failing or duplicating the bar.
    """
    if not candles:
        return 0
    rows = [
        {
            "asset_id": asset_id,
            "interval": interval,
            "timestamp": candle["timestamp"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle["volume"],
        }
        for candle in candles
    ]
    stmt = pg_insert(HistoricalPrice).values(rows)
    db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_historical_prices_asset_interval_ts",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
    )
    db.flush()
    return len(rows)


def list_candles(
    db: Session,
    asset_id: int,
    interval: CandleInterval,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
) -> list[HistoricalPrice]:
    """Newest-first window of candles. The caller reverses for chronological chart rendering.

    Newest-first + LIMIT is what makes "the last N candles" cheap: it walks the DESC index and
    stops, instead of reading the whole range to find its tail.
    """
    stmt = select(HistoricalPrice).where(
        HistoricalPrice.asset_id == asset_id, HistoricalPrice.interval == interval
    )
    if start is not None:
        stmt = stmt.where(HistoricalPrice.timestamp >= start)
    if end is not None:
        stmt = stmt.where(HistoricalPrice.timestamp <= end)
    stmt = stmt.order_by(HistoricalPrice.timestamp.desc()).limit(limit)
    return list(db.scalars(stmt))


def latest_candle(
    db: Session, asset_id: int, interval: CandleInterval = CandleInterval.ONE_MINUTE
) -> HistoricalPrice | None:
    # Single step into ix_historical_prices_asset_interval_ts (timestamp DESC): no sort, no scan.
    return db.scalar(
        select(HistoricalPrice)
        .where(HistoricalPrice.asset_id == asset_id, HistoricalPrice.interval == interval)
        .order_by(HistoricalPrice.timestamp.desc())
        .limit(1)
    )
