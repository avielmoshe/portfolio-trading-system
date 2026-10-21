"""Execution pricing from Redis: the live tick wins, anything wrong with it is ignored, and the
platform degrades to stored candles instead of failing or trusting bad data.

Each test uses its own symbol, so ticks written here cannot leak into another test.
"""

import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.cache import keys
from app.cache.client import build_client, get_redis
from app.cache.prices import Tick, get_tick, write_ticks


def _tick(symbol: str, price: str, age_seconds: float = 0.0, prev_close: str | None = None) -> Tick:
    return Tick(
        symbol=symbol,
        price=Decimal(price),
        prev_close=Decimal(prev_close or price),
        ts=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


def _order(client, headers, symbol: str, action: str = "BUY", quantity: str = "1"):
    return client.post("/api/orders", headers=headers, json={"symbol": symbol, "action": action, "quantity": quantity})


# ---- the tick is preferred -----------------------------------------------------------------


def test_order_fills_at_the_live_tick_not_the_candle(client, new_user, make_asset) -> None:
    symbol = make_asset("RTICKWIN", "50")  # candle says 50
    write_ticks([_tick(symbol, "77.25")])  # live feed says 77.25
    _, headers = new_user("tickwin")

    body = _order(client, headers, symbol).json()

    assert Decimal(body["price_per_share"]) == Decimal("77.25")
    assert body["price_source"] == "redis"
    assert body["price_interval"] is None
    assert Decimal(body["cash_balance"]) == Decimal("10000") - Decimal("77.25")
    assert Decimal(body["transaction"]["price_per_share"]) == Decimal("77.25")


def test_quote_and_order_agree_on_source_and_price(client, new_user, make_asset) -> None:
    symbol = make_asset("RQUOTE", "10")
    write_ticks([_tick(symbol, "12.5")])
    _, headers = new_user("quote")

    quote = client.get(f"/api/assets/{symbol}/quote", headers=headers).json()
    order = _order(client, headers, symbol).json()

    assert quote["source"] == order["price_source"] == "redis"
    assert Decimal(quote["price"]) == Decimal(order["price_per_share"]) == Decimal("12.5")


def test_the_newest_tick_replaces_the_previous_one(client, new_user, make_asset) -> None:
    symbol = make_asset("RNEWEST", "10")
    write_ticks([_tick(symbol, "20")])
    write_ticks([_tick(symbol, "21.5")])
    _, headers = new_user("newest")

    assert Decimal(_order(client, headers, symbol).json()["price_per_share"]) == Decimal("21.5")


def test_tick_precision_survives_the_round_trip_exactly(client, new_user, make_asset) -> None:
    symbol = make_asset("RPRECISE", "10")
    # 18 significant digits: a float keeps only ~16, so a float round trip would corrupt the last ones.
    exact = "1234567890.12345678"
    assert str(float(Decimal(exact))) != exact, "test value no longer distinguishes float from Decimal"
    write_ticks([_tick(symbol, exact)])
    assert get_redis().hget(keys.price_key(symbol), "price") == exact, "must be stored as text"
    _, headers = new_user("precise")

    quote = client.get(f"/api/assets/{symbol}/quote", headers=headers).json()
    assert quote["price"] == exact


# ---- a bad or missing tick is never trusted ------------------------------------------------


def test_a_stale_tick_is_ignored_and_the_candle_is_used(client, new_user, make_asset) -> None:
    symbol = make_asset("RSTALE", "40")
    write_ticks([_tick(symbol, "999", age_seconds=120)])  # far older than MAX_TICK_AGE
    _, headers = new_user("stale")

    body = _order(client, headers, symbol).json()

    assert body["price_source"] == "candle"
    assert Decimal(body["price_per_share"]) == Decimal("40"), "a stale 999 must never become the fill price"


def test_a_future_dated_tick_is_ignored(client, new_user, make_asset) -> None:
    """A frozen price with a far-future timestamp would otherwise look fresh forever."""
    symbol = make_asset("RFUTURE", "30")
    write_ticks([_tick(symbol, "999", age_seconds=-3600)])
    _, headers = new_user("future")

    body = _order(client, headers, symbol).json()
    assert body["price_source"] == "candle"
    assert Decimal(body["price_per_share"]) == Decimal("30")


@pytest.mark.parametrize(
    "fields",
    [
        {"price": "not-a-number", "prev_close": "1", "ts": "NOW"},
        {"price": "-5", "prev_close": "1", "ts": "NOW"},
        {"price": "0", "prev_close": "1", "ts": "NOW"},
        {"price": "NaN", "prev_close": "1", "ts": "NOW"},
        {"price": "Infinity", "prev_close": "1", "ts": "NOW"},
        {"price": "5", "prev_close": "1", "ts": "yesterday-ish"},
        {"price": "5", "prev_close": "1"},  # no ts
        {"prev_close": "1", "ts": "NOW"},  # no price
    ],
)
def test_malformed_ticks_never_set_a_price(client, new_user, make_asset, fields) -> None:
    symbol = make_asset(f"RBAD{abs(hash(str(sorted(fields.items())))) % 100_000}", "25")
    fields = {k: (datetime.now(UTC).isoformat() if v == "NOW" else v) for k, v in fields.items()}
    get_redis().hset(keys.price_key(symbol), mapping=fields)
    assert get_tick(symbol) is None
    _, headers = new_user("malformed")

    body = _order(client, headers, symbol).json()
    assert body["price_source"] == "candle"
    assert Decimal(body["price_per_share"]) == Decimal("25")


def test_an_expired_key_falls_back_to_the_candle(client, new_user, make_asset) -> None:
    symbol = make_asset("REXPIRE", "15")
    write_ticks([_tick(symbol, "88")])
    get_redis().delete(keys.price_key(symbol))  # what TTL expiry does
    _, headers = new_user("expire")

    body = _order(client, headers, symbol).json()
    assert body["price_source"] == "candle"
    assert Decimal(body["price_per_share"]) == Decimal("15")


def test_no_tick_and_no_candle_is_refused_not_guessed(client, new_user, admin_headers) -> None:
    created = client.post("/api/assets", headers=admin_headers, json={"symbol": "RNOPRICE", "company_name": "No Price Co"})
    assert created.status_code == 201, created.text
    _, headers = new_user("noprice")

    response = _order(client, headers, "RNOPRICE")

    assert response.status_code == 409
    assert response.json()["detail"] == "no price data for this asset"
    assert client.get("/api/users/me", headers=headers).json()["cash_balance"] == "10000.00000000"


def test_the_client_still_cannot_set_a_price(client, new_user, make_asset) -> None:
    symbol = make_asset("RNOCLIENT", "60")
    write_ticks([_tick(symbol, "60")])
    _, headers = new_user("noclient")
    response = client.post(
        "/api/orders",
        headers=headers,
        json={"symbol": symbol, "action": "BUY", "quantity": "1", "price_per_share": "0.01"},
    )
    assert response.status_code == 422


# ---- Redis outage --------------------------------------------------------------------------


def test_orders_survive_a_redis_outage_by_using_the_candle(client, new_user, make_asset, monkeypatch) -> None:
    symbol = make_asset("ROUTAGE", "33")
    write_ticks([_tick(symbol, "99")])  # a tick exists, but Redis will be unreachable
    _, headers = new_user("outage")

    # Port 1 refuses connections, standing in for a dead Redis. Built with the PRODUCTION client
    # factory on purpose: a hand-rolled client here would test its own timeouts, not the shipped ones
    # (this is how the redis-py default retry/backoff stall was found).
    dead = build_client("redis://127.0.0.1:1/0")
    monkeypatch.setattr("app.cache.prices.get_redis", lambda: dead)

    response = _order(client, headers, symbol)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["price_source"] == "candle"
    assert Decimal(body["price_per_share"]) == Decimal("33"), "must not use the unreachable tick"


def test_a_dead_redis_fails_fast_instead_of_hanging(monkeypatch) -> None:
    """Times only the Redis call, never the whole request, so database or CPU load elsewhere in the
    process cannot make it flaky. The bound is generous (the configured timeout is 0.5s)."""
    dead = build_client("redis://127.0.0.1:1/0")
    monkeypatch.setattr("app.cache.prices.get_redis", lambda: dead)

    started = time.monotonic()
    assert get_tick("ANYTHING") is None
    elapsed = time.monotonic() - started

    assert elapsed < 2, f"reading from a dead Redis took {elapsed:.1f}s; the order path would stall"


def test_health_reports_redis_as_degraded_without_failing(client, monkeypatch) -> None:
    healthy = client.get("/health")
    assert healthy.status_code == 200
    assert healthy.json()["redis"] == "connected"

    dead = build_client("redis://127.0.0.1:1/0")
    monkeypatch.setattr("app.cache.client.get_redis", lambda: dead)
    degraded = client.get("/health")
    assert degraded.status_code == 200, "a cache outage must not fail the health probe"
    assert degraded.json()["redis"] == "unavailable"


# ---- the cache core ------------------------------------------------------------------------


def test_keys_expire_so_a_dead_feeder_stops_serving_prices() -> None:
    write_ticks([_tick("RTTL", "10")])
    ttl = get_redis().ttl(keys.price_key("RTTL"))
    assert 0 < ttl <= keys.PRICE_TTL_SECONDS, f"key has no usable TTL (ttl={ttl})"


def test_every_tick_is_published_for_the_websocket_layer() -> None:
    """Pub/Sub channels are NOT isolated per Redis database: the dev feeder (db 0) publishes on the
    same `ticks` channel this test (db 1) subscribes to. So the test must pick out its own message
    from the stream instead of assuming the first one is its own."""
    pubsub = get_redis().pubsub()
    pubsub.subscribe(keys.TICKS_CHANNEL)
    try:
        # Wait for Redis to confirm the subscription; publishing before that loses the message.
        deadline = time.monotonic() + 3
        confirmed = False
        while time.monotonic() < deadline and not confirmed:
            message = pubsub.get_message(timeout=0.2)
            confirmed = bool(message) and message["type"] == "subscribe"
        assert confirmed, "never received the subscribe confirmation"

        write_ticks([_tick("RPUBSUB", "42.5", prev_close="40")])

        found = None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and found is None:
            message = pubsub.get_message(timeout=0.2)
            if message and message["type"] == "message" and '"symbol": "RPUBSUB"' in message["data"]:
                found = message
    finally:
        pubsub.close()
    assert found is not None, "our tick was never published"
    assert '"price": "42.5"' in found["data"]
    assert '"prev_close": "40"' in found["data"]


def test_symbol_lookup_is_case_insensitive() -> None:
    write_ticks([_tick("RCASE", "7")])
    assert get_tick("rcase") is not None
    assert get_tick("RCASE").price == Decimal("7")


# ---- the mock feeder's price walk ----------------------------------------------------------


def test_feeder_step_stays_positive_and_on_scale_even_under_extreme_moves(monkeypatch) -> None:
    from app.workers import price_feeder

    monkeypatch.setattr(random, "gauss", lambda _mu, _sigma: -50.0)  # a -5000% "move"
    price = price_feeder._step(Decimal("100"))
    assert price == price_feeder._MIN_PRICE, "the floor must stop the walk reaching zero or below"

    monkeypatch.setattr(random, "gauss", lambda _mu, _sigma: 0.0123456789)
    stepped = price_feeder._step(Decimal("100"))
    assert stepped == stepped.quantize(Decimal("0.00000001")), "price must fit Numeric(18,8)"


def test_feeder_walk_never_violates_the_candle_constraints() -> None:
    from app.workers import price_feeder

    price = Decimal("100")
    for _ in range(5000):
        price = price_feeder._step(price)
        assert price > 0
