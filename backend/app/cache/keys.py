"""Every Redis key and channel name in one place, so the writer (feeder) and the readers
(order pricing, later the WebSocket) can never disagree on spelling or format."""

# Live price per ticker: a hash {price, prev_close, ts}. Expires on its own (see TTL below), so a
# feeder that dies stops serving prices within a minute instead of serving the last one forever.
PRICE_TTL_SECONDS = 60

# Pub/Sub channel carrying every tick as JSON, for the future WebSocket fan-out. Pub/Sub is
# fire-and-forget: a subscriber that is offline simply misses ticks, which is correct for prices
# (only the latest matters) and is why the hash above, not this channel, is the source for orders.
TICKS_CHANNEL = "ticks"


def price_key(symbol: str) -> str:
    return f"price:{symbol.upper()}"
