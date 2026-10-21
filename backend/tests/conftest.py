"""Test fixtures for the E2E suite.

Two things here are not optional:

1. `DATABASE_URL` is rewritten to a dedicated test database BEFORE any `app.*` import, because
   `app/core/database.py` builds the engine at import time — importing the app first would bind
   it to the development database for the whole process.
2. The schema is built by running the Alembic migrations, never `Base.metadata.create_all()`.
   The ledger's append-only trigger exists only in migration 0008, so `create_all` would
   produce a schema that silently allows UPDATE/DELETE on `transactions` and the suite would
   pass against a table that does not match production.

The test database is dropped and recreated per session. That is also the only way to clean up
after these tests at all: the trigger blocks DELETE and TRUNCATE on `transactions`, and users
are referenced with ON DELETE RESTRICT, so individual rows are permanent by design.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "trading_test")

_default = "postgresql://trading_user:trading_pass@db:5432/trading_db"
_parts = urlparse(os.getenv("DATABASE_URL") or _default)
TEST_DATABASE_URL = urlunparse(_parts._replace(path=f"/{TEST_DB_NAME}"))
MAINTENANCE_URL = urlunparse(_parts._replace(path="/postgres"))

# Redis database 1 for tests; the dev feeder writes database 0. Flushed below, so a test can
# never read a tick the mock feeder happened to publish, and never delete a dev one.
_redis = urlparse(os.getenv("REDIS_URL") or "redis://redis:6379/0")
TEST_REDIS_URL = urlunparse(_redis._replace(path="/1"))

# Must precede every app import in this process.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-characters-long")
# Candles seeded below are minutes old; keep the freshness window generous so a slow run
# cannot turn into a spurious "price is stale" failure.
os.environ.setdefault("MAX_PRICE_AGE_SECONDS", "86400")

TEST_SYMBOL = "E2E"


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    connection = psycopg2.connect(MAINTENANCE_URL)
    connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with connection.cursor() as cursor:
            # WITH (FORCE) terminates leftover sessions from an interrupted run (Postgres 13+),
            # which would otherwise make DROP DATABASE hang forever.
            cursor.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        connection.close()

    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def _test_redis() -> None:
    import redis

    connection = redis.Redis.from_url(TEST_REDIS_URL)
    try:
        connection.flushdb()
    finally:
        connection.close()


@pytest.fixture(scope="session")
def client(_test_database: None):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def admin_headers(client) -> dict[str, str]:
    """An admin, needed to seed the catalogue and candles.

    Promotion is done with SQL on purpose: there is deliberately no API route that grants admin
    rights, so a test must not be able to invent one.
    """
    email = "qa-admin@example.com"
    password = "admin-password-123"
    created = client.post("/api/users/createUser", json={"email": email, "password": password})
    assert created.status_code == 200, created.text

    from sqlalchemy import text

    from app.core.database import engine

    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET is_admin = true WHERE email = :email"), {"email": email})

    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def seeded_asset(client, admin_headers: dict[str, str]) -> str:
    """Catalogue entry plus fresh 1-minute candles, so orders have a server-resolved price."""
    created = client.post(
        "/api/assets",
        headers=admin_headers,
        json={
            "symbol": TEST_SYMBOL,
            "company_name": "E2E Test Instrument",
            "sector": "Testing",
            "description": "Synthetic asset used by the end-to-end suite.",
        },
    )
    assert created.status_code == 201, created.text

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    candles = [
        {
            "timestamp": (now - timedelta(minutes=offset)).isoformat(),
            "open": "100.00",
            "high": "104.00",
            "low": "99.00",
            # Newest candle closes at 102.50 — the price every order in the suite should fill at.
            "close": "102.50" if offset == 0 else "101.00",
            "volume": 1_000 + offset,
        }
        for offset in range(9, -1, -1)
    ]
    imported = client.post(
        f"/api/assets/{TEST_SYMBOL}/prices",
        headers=admin_headers,
        json={"interval": "1m", "candles": candles},
    )
    assert imported.status_code == 200, imported.text
    return TEST_SYMBOL


def _fresh_minute() -> str:
    return datetime.now(UTC).replace(second=0, microsecond=0).isoformat()


@pytest.fixture(scope="session")
def set_price(client, admin_headers: dict[str, str]):
    """Set an asset's execution price by upserting the current-minute 1m candle.

    The upsert key is (asset, interval, timestamp), so calling this again within the same
    minute overwrites the close rather than adding a candle. open=high=low=close keeps the
    candle valid under the DB's OHLC constraints.
    """

    def _set(symbol: str, price: str) -> None:
        response = client.post(
            f"/api/assets/{symbol}/prices",
            headers=admin_headers,
            json={
                "interval": "1m",
                "candles": [
                    {"timestamp": _fresh_minute(), "open": price, "high": price, "low": price, "close": price, "volume": 1}
                ],
            },
        )
        assert response.status_code == 200, response.text

    return _set


@pytest.fixture(scope="session")
def make_asset(client, admin_headers: dict[str, str], set_price):
    """Create a tradable asset priced at `price`. Symbols must be unique per call."""

    def _make(symbol: str, price: str) -> str:
        created = client.post(
            "/api/assets",
            headers=admin_headers,
            json={"symbol": symbol, "company_name": f"{symbol} Test Co"},
        )
        assert created.status_code == 201, created.text
        set_price(symbol, price)
        return symbol

    return _make


@pytest.fixture
def new_user(client):
    """Factory for a registered, logged-in user. Returns (email, auth_headers)."""
    counter = {"n": 0}

    def _make(prefix: str = "user") -> tuple[str, dict[str, str]]:
        counter["n"] += 1
        email = f"{prefix}-{os.getpid()}-{counter['n']}-{datetime.now(UTC).timestamp():.6f}@example.com"
        password = "fixture-password-123"
        created = client.post("/api/users/createUser", json={"email": email, "password": password})
        assert created.status_code == 200, created.text
        token = client.post("/api/auth/login", json={"email": email, "password": password}).json()["access_token"]
        return email, {"Authorization": f"Bearer {token}"}

    return _make
