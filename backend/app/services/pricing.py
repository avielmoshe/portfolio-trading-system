import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.cache import prices as price_cache
from app.models.asset import Asset
from app.models.historical_price import CandleInterval
from app.services import historical_price as candle_service


class PriceUnavailableError(Exception):
    """No usable server-side price for this asset: never fall back to a client-supplied one."""


# A live tick older than this is not a live price. The Redis key also expires on its own after 60s;
# this tighter bound catches a feeder that is alive but lagging, which the TTL would not.
MAX_TICK_AGE = timedelta(seconds=float(os.getenv("MAX_TICK_AGE_SECONDS", "10")))
# A tick dated in the future is a clock-skew or corruption symptom, and trusting it would keep a
# frozen price looking fresh. A little tolerance is allowed for normal clock differences.
_FUTURE_TOLERANCE = timedelta(seconds=5)

# Fallback only: how old a stored candle may be when Redis has no usable tick. Far more generous
# than the tick bound because candles are history, not a live feed. Using this path means the fill
# price is NOT current, which is why the source is reported on every quote and order.
MAX_PRICE_AGE = timedelta(seconds=int(os.getenv("MAX_PRICE_AGE_SECONDS", str(24 * 3600))))

# Finest interval first: the freshest candle wins, and coarser ones are a fallback for assets
# that only carry daily data.
_RESOLUTION_ORDER = (
    CandleInterval.ONE_MINUTE,
    CandleInterval.FIVE_MINUTES,
    CandleInterval.FIFTEEN_MINUTES,
    CandleInterval.ONE_HOUR,
    CandleInterval.ONE_DAY,
)

PriceSource = Literal["redis", "candle"]


@dataclass(frozen=True)
class ResolvedPrice:
    price: Decimal
    as_of: datetime
    source: PriceSource
    # Only meaningful for candle prices; a live tick has no aggregation interval.
    interval: CandleInterval | None


def _from_redis(asset: Asset, reference: datetime) -> ResolvedPrice | None:
    tick = price_cache.get_tick(asset.symbol)
    if tick is None:
        return None
    age = reference - tick.ts
    if age > MAX_TICK_AGE or age < -_FUTURE_TOLERANCE:
        return None
    return ResolvedPrice(price=tick.price, as_of=tick.ts, source="redis", interval=None)


def _from_candles(db: Session, asset: Asset, reference: datetime) -> ResolvedPrice:
    freshest = None
    for interval in _RESOLUTION_ORDER:
        candle = candle_service.latest_candle(db, asset.id, interval)
        if candle is None:
            continue
        if freshest is None or candle.timestamp > freshest.timestamp:
            freshest = candle
        # A finer candle inside the window is as good as it gets; stop rather than query coarser ones.
        if reference - candle.timestamp <= MAX_PRICE_AGE:
            return ResolvedPrice(price=candle.close, as_of=candle.timestamp, source="candle", interval=candle.interval)
    if freshest is None:
        raise PriceUnavailableError("no price data for this asset")
    raise PriceUnavailableError(
        f"latest price is stale (candle at {freshest.timestamp.isoformat()}, "
        f"max age {MAX_PRICE_AGE.total_seconds():.0f}s)"
    )


def resolve_execution_price(db: Session, asset: Asset, now: datetime | None = None) -> ResolvedPrice:
    """The price a trade executes at, decided entirely by the server.

    The client cannot influence this: an order request carries no price field at all, which is
    what stops a caller from buying at 0.01. Order of preference:

      1. The live Redis tick, if present and fresh.
      2. The latest stored OHLCV close, if Redis has nothing usable (feed down, key expired,
         Redis unreachable). The platform degrades to a staler price rather than refusing to trade.
      3. Otherwise PriceUnavailableError — refusing a trade is recoverable, filling one at a wrong
         price is not.

    A Redis price may set the EXECUTION price only. It is never used to decide whether the user can
    afford the trade: cash and quantities are re-read FOR UPDATE from Postgres inside the trade
    transaction, because a cached balance stale by milliseconds is how a double-spend happens.
    """
    reference = now or datetime.now(UTC)
    return _from_redis(asset, reference) or _from_candles(db, asset, reference)
