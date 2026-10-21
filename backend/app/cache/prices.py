import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import redis

from app.cache import keys
from app.cache.client import get_redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: Decimal
    prev_close: Decimal
    ts: datetime


def write_ticks(ticks: list[Tick]) -> None:
    """Store the latest price for each symbol and announce it, in one network round trip.

    A pipeline sends all commands together instead of waiting for a reply to each; with an
    asset per tick that is the difference between N round trips a second and one.
    """
    if not ticks:
        return
    client = get_redis()
    pipe = client.pipeline(transaction=False)
    for tick in ticks:
        key = keys.price_key(tick.symbol)
        # Decimals go in as strings: Redis stores text, and float() would reintroduce exactly
        # the binary rounding error Numeric(18,8) exists to prevent.
        pipe.hset(
            key,
            mapping={
                "price": str(tick.price),
                "prev_close": str(tick.prev_close),
                "ts": tick.ts.isoformat(),
            },
        )
        # Re-armed on every write: the key lives 60s after the LAST tick, so it vanishes when
        # the feed stops rather than quietly serving an old price.
        pipe.expire(key, keys.PRICE_TTL_SECONDS)
        pipe.publish(
            keys.TICKS_CHANNEL,
            json.dumps(
                {
                    "symbol": tick.symbol,
                    "price": str(tick.price),
                    "prev_close": str(tick.prev_close),
                    "ts": tick.ts.isoformat(),
                }
            ),
        )
    pipe.execute()


def get_tick(symbol: str) -> Tick | None:
    """Latest tick for a symbol, or None if absent, expired, malformed, or Redis is down.

    A cache miss is normal and a Redis outage is survivable, so neither raises: the caller falls
    back to Postgres. Returning None for a malformed entry also means a corrupted key can never
    set an execution price.
    """
    try:
        raw = get_redis().hgetall(keys.price_key(symbol))
    except redis.RedisError as exc:
        logger.warning("redis unavailable while reading tick for %s: %s", symbol, exc)
        return None
    if not raw:
        return None
    try:
        price = Decimal(raw["price"])
        prev_close = Decimal(raw["prev_close"])
        ts = datetime.fromisoformat(raw["ts"])
    except (KeyError, InvalidOperation, ValueError):
        logger.warning("discarding malformed tick for %s: %r", symbol, raw)
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if not price.is_finite() or price <= 0:
        return None
    return Tick(symbol=symbol.upper(), price=price, prev_close=prev_close, ts=ts)
