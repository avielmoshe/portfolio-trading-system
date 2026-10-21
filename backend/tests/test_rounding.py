"""Money arithmetic: exact decimals, one rounding rule, no drift between what the user is
charged, what the ledger says, and what the balance becomes.

Expected values are hard-coded literals worked out by hand, not recomputed with the same code
under test, so a wrong rounding rule cannot make the test agree with itself.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

START_BALANCE = Decimal("10000")
SCALE = Decimal("0.00000001")


def _buy(client, headers, symbol: str, quantity: str):
    return client.post("/api/orders", headers=headers, json={"symbol": symbol, "action": "BUY", "quantity": quantity})


def _sell(client, headers, symbol: str, quantity: str):
    return client.post("/api/orders", headers=headers, json={"symbol": symbol, "action": "SELL", "quantity": quantity})


def _balance(client, headers) -> Decimal:
    return Decimal(client.get("/api/users/me", headers=headers).json()["cash_balance"])


# ---- binary floats would fail these --------------------------------------------------------


def test_deposits_of_a_tenth_and_two_tenths_sum_exactly(client, new_user) -> None:
    """In binary floating point 0.1 + 0.2 == 0.30000000000000004. Balances must not."""
    _, headers = new_user("floatcheck")
    client.post("/api/users/me/deposit", headers=headers, json={"amount": "0.1"})
    client.post("/api/users/me/deposit", headers=headers, json={"amount": "0.2"})
    assert _balance(client, headers) == Decimal("10000.30000000")

    client.post("/api/users/me/withdraw", headers=headers, json={"amount": "0.3"})
    assert _balance(client, headers) == START_BALANCE


def test_a_hundred_tiny_deposits_do_not_drift(client, new_user) -> None:
    _, headers = new_user("tiny")
    for _ in range(100):
        assert client.post("/api/users/me/deposit", headers=headers, json={"amount": "0.01"}).status_code == 200
    assert _balance(client, headers) == Decimal("10001.00000000")


def test_balances_are_serialised_as_strings_not_floats(client, new_user) -> None:
    _, headers = new_user("serialise")
    raw = client.get("/api/users/me", headers=headers).json()["cash_balance"]
    assert isinstance(raw, str), "a JSON number would be parsed into a float by most clients"
    assert raw == "10000.00000000"


# ---- cost and proceeds rounding ------------------------------------------------------------


def test_trade_cost_is_rounded_once_to_eight_places(client, new_user, make_asset) -> None:
    """0.33333333 x 3.00000001 = 0.9999999933333333 exactly; the charge is that rounded to 8dp."""
    symbol = make_asset("RNDCOST", "3.00000001")
    _, headers = new_user("cost")

    body = _buy(client, headers, symbol, "0.33333333").json()

    assert Decimal(body["total"]) == Decimal("0.99999999")
    assert _balance(client, headers) == Decimal("9999.00000001")
    assert Decimal(body["cash_balance"]) == Decimal("9999.00000001")


def test_a_half_unit_in_the_last_place_rounds_up(client, new_user, make_asset) -> None:
    """0.5 x 0.00000001 = 0.000000005, exactly halfway: ROUND_HALF_UP gives 0.00000001, whereas
    banker's rounding would give 0 and let a user trade for free."""
    symbol = make_asset("RNDHALF", "0.00000001")
    _, headers = new_user("half")

    body = _buy(client, headers, symbol, "0.5").json()

    assert Decimal(body["total"]) == Decimal("0.00000001")
    assert _balance(client, headers) == Decimal("9999.99999999")


def test_charge_ledger_and_balance_agree_to_the_last_digit(client, new_user, make_asset) -> None:
    symbol = make_asset("RNDLEDGER", "7.77777777")
    _, headers = new_user("agree")

    bought = _buy(client, headers, symbol, "1.23456789").json()
    sold = _sell(client, headers, symbol, "0.11111111").json()

    items = client.get("/api/transactions", headers=headers).json()["items"]
    ledger_by_action = {item["action"]: Decimal(item["total"]) for item in items}
    assert ledger_by_action["BUY"] == Decimal(bought["total"])
    assert ledger_by_action["SELL"] == Decimal(sold["total"])

    # 1.23456789 x 7.77777777 = 9.6021...; verify with exact rational arithmetic, independent
    # of the Decimal code path under test.
    exact_buy = Fraction("1.23456789") * Fraction("7.77777777")
    assert abs(Fraction(ledger_by_action["BUY"]) - exact_buy) <= Fraction(5, 10**9)

    expected_balance = START_BALANCE - ledger_by_action["BUY"] + ledger_by_action["SELL"]
    assert _balance(client, headers) == expected_balance


# ---- average buy price ---------------------------------------------------------------------


def test_average_price_of_a_non_terminating_division_is_rounded_half_up(client, new_user, make_asset, set_price) -> None:
    """1 share at 10 then 2 at 11: (10 + 22) / 3 = 10.666666... -> 10.66666667."""
    symbol = make_asset("RNDAVG", "10")
    _, headers = new_user("avg")

    assert _buy(client, headers, symbol, "1").status_code == 201
    set_price(symbol, "11")
    body = _buy(client, headers, symbol, "2").json()

    assert Decimal(body["position"]["total_quantity"]) == 3
    assert Decimal(body["position"]["average_buy_price"]) == Decimal("10.66666667")


def test_average_is_a_clean_number_when_the_division_is_exact(client, new_user, make_asset, set_price) -> None:
    symbol = make_asset("RNDEXACT", "100")
    _, headers = new_user("exact")
    assert _buy(client, headers, symbol, "10").status_code == 201
    set_price(symbol, "130")
    body = _buy(client, headers, symbol, "5").json()
    # (10x100 + 5x130) / 15 = 1650 / 15 = 110
    assert Decimal(body["position"]["average_buy_price"]) == Decimal("110.00000000")


def test_selling_never_changes_the_average_cost(client, new_user, make_asset, set_price) -> None:
    symbol = make_asset("RNDSELL", "10")
    _, headers = new_user("sellavg")
    assert _buy(client, headers, symbol, "1").status_code == 201
    set_price(symbol, "11")
    assert _buy(client, headers, symbol, "2").status_code == 201

    set_price(symbol, "250")
    body = _sell(client, headers, symbol, "1").json()

    assert Decimal(body["position"]["total_quantity"]) == 2
    assert Decimal(body["position"]["average_buy_price"]) == Decimal("10.66666667")


def test_average_price_drift_stays_within_a_bounded_error(client, new_user, make_asset, set_price) -> None:
    """The average is recomputed from its own previously-rounded value on every buy, so each
    buy can add up to half a unit of the last place (5e-9) of error. This pins the bound: after
    N buys the stored average must be within N x 5e-9 of the exact weighted mean.

    If this ever fails, the rounding is no longer bounded and cost basis should be stored as a
    running total rather than recomputed from the rounded average."""
    symbol = make_asset("RNDDRIFT", "1")
    _, headers = new_user("drift")
    # Irregular prices on purpose: an arithmetic progression has a terminating mean, which would
    # hide every rounding error this test exists to measure.
    prices = [f"{Decimal(5000 + i * 7919 % 9973) / 100:.2f}" for i in range(37)]

    total_cost = Fraction(0)
    total_qty = Fraction(0)
    last = None
    for price in prices:
        set_price(symbol, price)
        response = _buy(client, headers, symbol, "1.7")
        assert response.status_code == 201, response.text
        total_cost += Fraction("1.7") * Fraction(price)
        total_qty += Fraction("1.7")
        last = response.json()

    stored = Fraction(last["position"]["average_buy_price"])
    exact = total_cost / total_qty
    error = abs(stored - exact)
    assert error > 0, "test is blind: the exact mean terminates, so no rounding was exercised"
    assert error <= Fraction(len(prices) * 5, 10**9), f"drift {float(error):.3e} exceeds the bound"


# ---- input precision -----------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0.123456789", "1.000000001"])
def test_amounts_with_more_than_eight_decimals_are_rejected(client, new_user, amount) -> None:
    """Accepting them would silently round the user's money; reject so nothing is lost quietly."""
    _, headers = new_user("precision")
    assert client.post("/api/users/me/deposit", headers=headers, json={"amount": amount}).status_code == 422
    assert client.post("/api/users/me/withdraw", headers=headers, json={"amount": amount}).status_code == 422
    assert _balance(client, headers) == START_BALANCE


@pytest.mark.parametrize("quantity", ["0", "-1", "0.000000001", "abc"])
def test_invalid_order_quantities_are_rejected(client, new_user, make_asset, quantity) -> None:
    symbol = make_asset(f"RNDQ{abs(hash(quantity)) % 10_000}", "10")
    _, headers = new_user("badqty")
    assert _buy(client, headers, symbol, quantity).status_code == 422
    assert _balance(client, headers) == START_BALANCE


@pytest.mark.parametrize("amount", ["0", "-5", "100000001"])
def test_deposit_amounts_outside_the_allowed_range_are_rejected(client, new_user, amount) -> None:
    _, headers = new_user("range")
    assert client.post("/api/users/me/deposit", headers=headers, json={"amount": amount}).status_code == 422
