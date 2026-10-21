"""End-to-end trading journey, run in chronological order.

Each step depends on the one before it, so the tests share module state and must execute in
definition order (pytest's default; randomisation is disabled in pytest.ini). A failure early
in the journey will cascade — that is intended, because step 5 cannot be meaningful if step 2
never issued a token.

The suite asserts on outcomes a user would notice: the balance after each step, the price the
order actually filled at, and whether the ledger can be replayed to reproduce the final cash.
"""

from decimal import Decimal

import pytest

EMAIL = "qa-journey@example.com"
PASSWORD = "journey-password-123"

START_BALANCE = Decimal("10000")
DEPOSIT = Decimal("5000")
WITHDRAWAL = Decimal("1000")
BUY_QUANTITY = Decimal("10")
SELL_QUANTITY = Decimal("4")

# Carries the token, the discovered price and the running balance between steps.
state: dict[str, object] = {}


def _auth() -> dict[str, str]:
    token = state.get("token")
    assert token, "no access token: step 2 (login) must run first"
    return {"Authorization": f"Bearer {token}"}


def test_01_register_new_user(client) -> None:
    response = client.post("/api/users/createUser", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == EMAIL
    assert Decimal(body["cash_balance"]) == START_BALANCE
    assert body["is_admin"] is False
    # The hash must never leave the server.
    assert "hashed_password" not in body
    state["user_id"] = body["id"]


def test_02_login_returns_jwt(client) -> None:
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"].count(".") == 2, "not a three-part JWT"
    state["token"] = body["access_token"]

    # The token must actually authorise a protected route.
    me = client.get("/api/users/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == EMAIL


def test_03_deposit_cash(client) -> None:
    response = client.post("/api/users/me/deposit", headers=_auth(), json={"amount": str(DEPOSIT)})
    assert response.status_code == 200, response.text
    balance = Decimal(response.json()["cash_balance"])
    assert balance == START_BALANCE + DEPOSIT
    state["balance"] = balance


def test_04_fetch_historical_prices(client, seeded_asset: str) -> None:
    response = client.get(
        f"/api/assets/{seeded_asset}/prices", headers=_auth(), params={"interval": "1m", "limit": 10}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == seeded_asset
    assert body["interval"] == "1m"
    candles = body["candles"]
    assert len(candles) == 10

    # Chronological order is the contract the chart depends on.
    timestamps = [candle["timestamp"] for candle in candles]
    assert timestamps == sorted(timestamps), "candles must be oldest-first"
    for candle in candles:
        high, low = Decimal(candle["high"]), Decimal(candle["low"])
        assert high >= low
        assert low <= Decimal(candle["open"]) <= high
        assert low <= Decimal(candle["close"]) <= high

    # The quote endpoint must agree with the newest candle's close, since that is what the
    # order endpoint will resolve the execution price to.
    expected = Decimal(candles[-1]["close"])
    quote = client.get(f"/api/assets/{seeded_asset}/quote", headers=_auth())
    assert quote.status_code == 200, quote.text
    assert Decimal(quote.json()["price"]) == expected
    state["price"] = expected


def test_05_execute_buy_order(client, seeded_asset: str) -> None:
    price = state["price"]
    assert isinstance(price, Decimal)

    # A client-supplied price must be refused outright, not quietly ignored.
    exploit = client.post(
        "/api/orders",
        headers=_auth(),
        json={"symbol": seeded_asset, "action": "BUY", "quantity": "1", "price_per_share": "0.01"},
    )
    assert exploit.status_code == 422, "client must not be able to set the execution price"

    response = client.post(
        "/api/orders",
        headers=_auth(),
        json={"symbol": seeded_asset, "action": "BUY", "quantity": str(BUY_QUANTITY)},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    cost = BUY_QUANTITY * price
    assert Decimal(body["price_per_share"]) == price, "must fill at the server-resolved price"
    assert Decimal(body["total"]) == cost
    assert Decimal(body["cash_balance"]) == state["balance"] - cost
    assert Decimal(body["position"]["total_quantity"]) == BUY_QUANTITY
    assert Decimal(body["position"]["average_buy_price"]) == price
    assert body["transaction"]["action"] == "BUY"
    assert Decimal(body["transaction"]["price_per_share"]) == price
    state["balance"] = Decimal(body["cash_balance"])

    holdings = client.get("/api/positions", headers=_auth())
    assert holdings.status_code == 200, holdings.text
    assert [h["asset"]["symbol"] for h in holdings.json()] == [seeded_asset]


def test_06_execute_sell_order(client, seeded_asset: str) -> None:
    price = state["price"]
    assert isinstance(price, Decimal)

    oversell = client.post(
        "/api/orders",
        headers=_auth(),
        json={"symbol": seeded_asset, "action": "SELL", "quantity": "99999"},
    )
    assert oversell.status_code == 400
    assert oversell.json()["detail"] == "Insufficient shares"

    response = client.post(
        "/api/orders",
        headers=_auth(),
        json={"symbol": seeded_asset, "action": "SELL", "quantity": str(SELL_QUANTITY)},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    proceeds = SELL_QUANTITY * price
    assert Decimal(body["total"]) == proceeds
    assert Decimal(body["cash_balance"]) == state["balance"] + proceeds
    assert Decimal(body["position"]["total_quantity"]) == BUY_QUANTITY - SELL_QUANTITY
    # A sale never changes the cost basis of what is still held.
    assert Decimal(body["position"]["average_buy_price"]) == price
    state["balance"] = Decimal(body["cash_balance"])


def test_07_withdraw_cash(client) -> None:
    too_much = client.post("/api/users/me/withdraw", headers=_auth(), json={"amount": "99999999"})
    assert too_much.status_code == 400
    assert too_much.json()["detail"] == "Insufficient funds"

    response = client.post(
        "/api/users/me/withdraw", headers=_auth(), json={"amount": str(WITHDRAWAL)}
    )
    assert response.status_code == 200, response.text
    balance = Decimal(response.json()["cash_balance"])
    assert balance == state["balance"] - WITHDRAWAL
    state["balance"] = balance


def test_08_ledger_records_every_operation(client, seeded_asset: str) -> None:
    response = client.get("/api/transactions", headers=_auth(), params={"limit": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    items = body["items"]

    # Newest first, and exactly the four movements this journey made.
    assert [item["action"] for item in items] == ["WITHDRAW", "SELL", "BUY", "DEPOSIT"]
    assert body["next_cursor"] is None

    by_action = {item["action"]: item for item in items}
    assert Decimal(by_action["DEPOSIT"]["total"]) == DEPOSIT
    assert Decimal(by_action["WITHDRAW"]["total"]) == WITHDRAWAL
    assert Decimal(by_action["BUY"]["quantity"]) == BUY_QUANTITY
    assert Decimal(by_action["SELL"]["quantity"]) == SELL_QUANTITY

    # Cash movements carry no instrument; trades must name one.
    assert by_action["DEPOSIT"]["asset"] is None
    assert by_action["WITHDRAW"]["asset"] is None
    assert by_action["BUY"]["asset"]["symbol"] == seeded_asset
    assert by_action["SELL"]["asset"]["symbol"] == seeded_asset

    # The ledger is the source of truth: replaying it must reproduce the live balance.
    replayed = START_BALANCE
    for item in items:
        amount = Decimal(item["total"])
        replayed += -amount if item["action"] in ("BUY", "WITHDRAW") else amount
    live = Decimal(client.get("/api/users/me", headers=_auth()).json()["cash_balance"])
    assert replayed == live == state["balance"], "ledger replay must equal the live balance"


def test_09_ledger_is_append_only(client) -> None:
    """The database must refuse to rewrite history, not merely lack an endpoint for it."""
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError

    from app.core.database import engine

    for statement in (
        "UPDATE transactions SET quantity = 1 WHERE id = (SELECT min(id) FROM transactions)",
        "DELETE FROM transactions WHERE id = (SELECT min(id) FROM transactions)",
        "TRUNCATE transactions",
    ):
        with pytest.raises(DatabaseError, match="append-only"):
            with engine.begin() as connection:
                connection.execute(text(statement))


def test_10_other_users_cannot_see_this_ledger(client) -> None:
    created = client.post(
        "/api/users/createUser", json={"email": "qa-outsider@example.com", "password": "outsider-123"}
    )
    assert created.status_code == 200, created.text
    token = client.post(
        "/api/auth/login", json={"email": "qa-outsider@example.com", "password": "outsider-123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/transactions", headers=headers).json()["items"] == []
    assert client.get("/api/positions", headers=headers).json() == []
    # And no token at all is rejected.
    assert client.get("/api/transactions").status_code == 401
