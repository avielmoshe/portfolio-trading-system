"""MOCK market-data feeder. Not a real price source.

Random-walks the price of every active asset and writes it to Redis once a second, standing in
for the real market-data pipeline (Kafka consumer) that does not exist yet. It exists so the
order path can be built and tested against the same Redis contract the real feed will use:
`price:{SYMBOL}` hash + `ticks` channel. When the real feed arrives, delete this module and the
`price-feeder` compose service; nothing else changes.

Run:  python -m app.workers.price_feeder
"""

import logging
import os
import random
import signal
import time
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from types import FrameType

import redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.cache.prices import Tick, write_ticks
from app.core.database import SessionLocal
from app.models.asset import Asset
from app.models.historical_price import HistoricalPrice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s feeder: %(message)s")
logger = logging.getLogger("price_feeder")

INTERVAL_SECONDS = float(os.getenv("FEEDER_INTERVAL_SECONDS", "1"))
# Std-dev of each step as a fraction of price: 0.001 = ~0.1% per second, visible but not wild.
VOLATILITY = float(os.getenv("FEEDER_VOLATILITY", "0.001"))
# Starting price for an asset that has no candle history to anchor on.
DEFAULT_START_PRICE = Decimal(os.getenv("FEEDER_DEFAULT_PRICE", "100"))
ASSET_REFRESH_SECONDS = 30
_SCALE = Decimal("0.00000001")
_MIN_PRICE = Decimal("0.01")

_running = True


def _stop(_signum: int, _frame: FrameType | None) -> None:
    global _running
    _running = False


def _load_active_assets() -> dict[str, tuple[Decimal, Decimal]]:
    """symbol -> (start_price, prev_close), anchored on the latest stored candle close so the mock
    prices continue from the same level the charts already show."""
    with SessionLocal() as db:
        symbols = list(db.execute(select(Asset.id, Asset.symbol).where(Asset.is_active.is_(True))))
        loaded: dict[str, tuple[Decimal, Decimal]] = {}
        for asset_id, symbol in symbols:
            latest = db.scalar(
                select(HistoricalPrice.close)
                .where(HistoricalPrice.asset_id == asset_id)
                .order_by(HistoricalPrice.timestamp.desc())
                .limit(1)
            )
            anchor = latest if latest is not None else DEFAULT_START_PRICE
            loaded[symbol] = (anchor, anchor)
        return loaded


def _step(price: Decimal) -> Decimal:
    # Multiplicative (not additive) so a low-priced asset cannot be walked below zero, and the
    # floor stops a long unlucky streak from reaching a price the DB constraints would reject.
    drift = Decimal(str(random.gauss(0, VOLATILITY)))
    return max((price * (Decimal(1) + drift)).quantize(_SCALE, rounding=ROUND_HALF_UP), _MIN_PRICE)


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("starting (interval=%ss volatility=%s) — MOCK DATA, not real prices", INTERVAL_SECONDS, VOLATILITY)

    prices: dict[str, Decimal] = {}
    prev_close: dict[str, Decimal] = {}
    last_refresh = 0.0

    while _running:
        started = time.monotonic()
        try:
            # Re-read the catalogue periodically so a newly added (or delisted) asset starts
            # (or stops) ticking without restarting the feeder.
            if started - last_refresh >= ASSET_REFRESH_SECONDS or not prices:
                assets = _load_active_assets()
                prices = {s: prices.get(s, start) for s, (start, _prev) in assets.items()}
                prev_close = {s: pc for s, (_start, pc) in assets.items()}
                last_refresh = started
                logger.info("tracking %d active asset(s): %s", len(prices), ", ".join(sorted(prices)) or "none")

            now = datetime.now(UTC)
            ticks = []
            for symbol in prices:
                prices[symbol] = _step(prices[symbol])
                ticks.append(Tick(symbol=symbol, price=prices[symbol], prev_close=prev_close[symbol], ts=now))
            write_ticks(ticks)
        except (redis.RedisError, SQLAlchemyError) as exc:
            # The feeder must outlive a restarting Redis or a Postgres that is still migrating,
            # so log and retry next cycle rather than crash-looping.
            logger.warning("cycle failed (%s: %s); retrying", type(exc).__name__, exc)
            last_refresh = 0.0

        # Sleep the remainder of the interval so a slow cycle doesn't make ticks drift later.
        time.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic() - started)))

    logger.info("stopped")


if __name__ == "__main__":
    run()
