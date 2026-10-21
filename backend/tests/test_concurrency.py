"""Concurrent requests against the real API and real Postgres.

Each worker thread makes its own request through its own TestClient, so the requests genuinely
overlap inside the server's thread pool and contend for the same database rows. A barrier
releases all workers at once to maximise overlap.

What is asserted is the invariant, not a lucky interleaving: money is never created or
destroyed, no balance or holding goes negative, nothing returns a 5xx, and the ledger replays
to the live cash balance. These would fail if row locking were removed from the trade path.
"""

import threading
from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

START_BALANCE = Decimal("10000")
JOIN_TIMEOUT_SECONDS = 60


def _run_parallel(jobs: list[Callable[[TestClient], tuple[int, dict]]]) -> list[tuple[int, dict]]:
    """Run every job at the same moment. Fails if any worker is still alive afterwards, which is
    how a deadlock would show up (a hung request, not an exception)."""
    from app.main import app

    results: list[tuple[int, dict] | None] = [None] * len(jobs)
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(jobs))

    def worker(index: int, job: Callable[[TestClient], tuple[int, dict]]) -> None:
        try:
            # No `with`: skips lifespan startup, which the session-level client already ran.
            local_client = TestClient(app)
            barrier.wait(timeout=30)
            results[index] = job(local_client)
        except BaseException as exc:  # noqa: BLE001 - surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_SECONDS)

    hung = [t for t in threads if t.is_alive()]
    assert not hung, f"{len(hung)} request(s) never completed: probable deadlock"
    assert not errors, f"worker raised: {errors[0]!r}"
    return [result for result in results if result is not None]


def _post(path: str, headers: dict[str, str], body: dict) -> Callable[[TestClient], tuple[int, dict]]:
    def job(local_client: TestClient) -> tuple[int, dict]:
        response = local_client.post(path, headers=headers, json=body)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"raw": response.text}

    return job


def _assert_no_server_errors(results: list[tuple[int, dict]]) -> None:
    server_errors = [(status, body) for status, body in results if status >= 500]
    assert not server_errors, f"server errors under concurrency: {server_errors}"


def _balance(client, headers: dict[str, str]) -> Decimal:
    return Decimal(client.get("/api/users/me", headers=headers).json()["cash_balance"])


def _ledger(client, headers: dict[str, str]) -> list[dict]:
    response = client.get("/api/transactions", headers=headers, params={"limit": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _replay(items: list[dict]) -> Decimal:
    balance = START_BALANCE
    for item in items:
        amount = Decimal(item["total"])
        balance += -amount if item["action"] in ("BUY", "WITHDRAW") else amount
    return balance


def test_parallel_buys_cannot_overspend_the_balance(client, new_user, make_asset) -> None:
    """12 buys at 1000 against 10000 cash: exactly 10 may succeed, never 11."""
    symbol = make_asset("CONCBUY", "1000")
    _, headers = new_user("overspend")

    results = _run_parallel([_post("/api/orders", headers, {"symbol": symbol, "action": "BUY", "quantity": "1"})] * 12)

    _assert_no_server_errors(results)
    statuses = sorted(status for status, _ in results)
    assert statuses.count(201) == 10, f"expected exactly 10 fills, got {statuses}"
    assert statuses.count(400) == 2
    assert all(body["detail"] == "Insufficient funds" for status, body in results if status == 400)

    assert _balance(client, headers) == 0
    position = client.get(f"/api/positions/{symbol}", headers=headers).json()
    assert Decimal(position["total_quantity"]) == 10
    buys = [item for item in _ledger(client, headers) if item["action"] == "BUY"]
    assert len(buys) == 10, "ledger must hold exactly one row per successful fill"


def test_parallel_sells_cannot_oversell_a_holding(client, new_user, make_asset) -> None:
    """Hold 5 shares, fire 9 sells of 1: exactly 5 may succeed."""
    symbol = make_asset("CONCSELL", "10")
    _, headers = new_user("oversell")
    bought = client.post("/api/orders", headers=headers, json={"symbol": symbol, "action": "BUY", "quantity": "5"})
    assert bought.status_code == 201, bought.text

    results = _run_parallel([_post("/api/orders", headers, {"symbol": symbol, "action": "SELL", "quantity": "1"})] * 9)

    _assert_no_server_errors(results)
    statuses = sorted(status for status, _ in results)
    assert statuses.count(201) == 5, f"expected exactly 5 sells, got {statuses}"
    assert statuses.count(400) == 4
    assert all(body["detail"] == "Insufficient shares" for status, body in results if status == 400)

    position = client.get(f"/api/positions/{symbol}", headers=headers).json()
    assert Decimal(position["total_quantity"]) == 0, "a holding must never go negative"
    # Everything bought was sold at the same price, so cash is back to where it started.
    assert _balance(client, headers) == START_BALANCE


def test_parallel_withdrawals_cannot_overdraw(client, new_user) -> None:
    """20 withdrawals of 1000 against 10000: exactly 10 succeed and the balance lands on 0."""
    _, headers = new_user("overdraw")

    results = _run_parallel([_post("/api/users/me/withdraw", headers, {"amount": "1000"})] * 20)

    _assert_no_server_errors(results)
    statuses = sorted(status for status, _ in results)
    assert statuses.count(200) == 10, f"expected exactly 10 withdrawals, got {statuses}"
    assert statuses.count(400) == 10
    assert _balance(client, headers) == 0


def test_parallel_first_buys_create_one_position_row(client, new_user, make_asset) -> None:
    """The user holds nothing yet. 10 simultaneous first buys must converge on one row, not
    crash on the unique constraint and not create duplicates."""
    symbol = make_asset("CONCFIRST", "50")
    _, headers = new_user("firstbuy")

    results = _run_parallel([_post("/api/orders", headers, {"symbol": symbol, "action": "BUY", "quantity": "2"})] * 10)

    _assert_no_server_errors(results)
    assert [status for status, _ in results] == [201] * 10, "every first-buy must succeed, none may lose the race"
    holdings = client.get("/api/positions", headers=headers).json()
    assert len(holdings) == 1, f"expected one position row, got {holdings}"
    assert Decimal(holdings[0]["total_quantity"]) == 20
    assert Decimal(holdings[0]["average_buy_price"]) == 50


def test_mixed_concurrent_operations_neither_deadlock_nor_lose_money(client, new_user, make_asset) -> None:
    """Buys, sells, deposits and withdrawals all at once across two assets. Lock ordering is the
    only thing preventing a deadlock here; _run_parallel fails if any request hangs."""
    first = make_asset("CONCMIXA", "20")
    second = make_asset("CONCMIXB", "30")
    _, headers = new_user("mixed")
    for symbol in (first, second):
        seed = client.post("/api/orders", headers=headers, json={"symbol": symbol, "action": "BUY", "quantity": "5"})
        assert seed.status_code == 201, seed.text

    jobs = []
    for i in range(32):
        symbol = first if i % 2 == 0 else second
        kind = i % 4
        if kind == 0:
            jobs.append(_post("/api/orders", headers, {"symbol": symbol, "action": "BUY", "quantity": "1"}))
        elif kind == 1:
            jobs.append(_post("/api/orders", headers, {"symbol": symbol, "action": "SELL", "quantity": "1"}))
        elif kind == 2:
            jobs.append(_post("/api/users/me/deposit", headers, {"amount": "100"}))
        else:
            jobs.append(_post("/api/users/me/withdraw", headers, {"amount": "40"}))

    results = _run_parallel(jobs)

    _assert_no_server_errors(results)
    live = _balance(client, headers)
    assert live >= 0
    for holding in client.get("/api/positions", headers=headers, params={"include_closed": "true"}).json():
        assert Decimal(holding["total_quantity"]) >= 0

    items = _ledger(client, headers)
    succeeded = sum(1 for status, _ in results if status in (200, 201))
    assert len(items) == succeeded + 2, "one ledger row per successful operation, plus the two seed buys"
    assert _replay(items) == live, "ledger replay must equal the live balance after concurrent activity"


@pytest.mark.parametrize("workers", [8])
def test_concurrent_registration_of_one_email_creates_exactly_one_account(client, workers) -> None:
    email = "concurrent-register@example.com"
    body = {"email": email, "password": "register-race-123"}

    results = _run_parallel([_post("/api/users/createUser", {}, body)] * workers)

    _assert_no_server_errors(results)
    statuses = sorted(status for status, _ in results)
    assert statuses.count(200) == 1, f"exactly one registration may win, got {statuses}"
    assert statuses.count(400) == workers - 1
    assert client.post("/api/auth/login", json=body).status_code == 200


# ---- the position lock on its own -----------------------------------------------------------
# Through the API every trade takes the user lock first, which already serialises one user's
# trades, so those tests cannot tell whether the position row lock works. These call the service
# directly with the user lock deliberately absent, so a regression in the position lock cannot
# hide behind the user lock.


def _service_workers(user_id: int, asset_id: int, jobs: int, action: str) -> list[str]:
    from app.core.database import SessionLocal
    from app.services import position as position_service

    outcomes: list[str] = [""] * jobs
    barrier = threading.Barrier(jobs)

    def worker(index: int) -> None:
        barrier.wait(timeout=30)
        with SessionLocal() as db:
            try:
                if action == "buy":
                    position_service.apply_buy(db, user_id, asset_id, Decimal("1"), Decimal("10"))
                else:
                    position_service.apply_sell(db, user_id, asset_id, Decimal("1"))
                db.commit()
                outcomes[index] = "ok"
            except position_service.InsufficientSharesError:
                db.rollback()
                outcomes[index] = "insufficient"
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                outcomes[index] = f"error:{type(exc).__name__}"

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_SECONDS)
    assert not any(t.is_alive() for t in threads), "position worker hung: probable deadlock"
    return outcomes


def _ids(client, headers: dict[str, str], symbol: str) -> tuple[int, int]:
    user_id = client.get("/api/users/me", headers=headers).json()["id"]
    asset_id = client.get(f"/api/assets/{symbol}", headers=headers).json()["id"]
    return user_id, asset_id


def test_position_lock_alone_prevents_lost_updates_on_buys(client, new_user, make_asset) -> None:
    symbol = make_asset("POSLOCKBUY", "10")
    _, headers = new_user("poslockbuy")
    user_id, asset_id = _ids(client, headers, symbol)

    outcomes = _service_workers(user_id, asset_id, jobs=20, action="buy")

    assert outcomes == ["ok"] * 20, outcomes
    holding = client.get(f"/api/positions/{symbol}", headers=headers).json()
    assert Decimal(holding["total_quantity"]) == 20, "a lost update would leave fewer than 20 shares"


def test_position_lock_alone_prevents_overselling(client, new_user, make_asset) -> None:
    symbol = make_asset("POSLOCKSELL", "10")
    _, headers = new_user("poslocksell")
    user_id, asset_id = _ids(client, headers, symbol)
    assert client.post("/api/orders", headers=headers, json={"symbol": symbol, "action": "BUY", "quantity": "5"}).status_code == 201

    outcomes = _service_workers(user_id, asset_id, jobs=9, action="sell")

    assert outcomes.count("ok") == 5, outcomes
    assert outcomes.count("insufficient") == 4, outcomes
    holding = client.get(f"/api/positions/{symbol}", headers=headers, params={}).json()
    assert Decimal(holding["total_quantity"]) == 0
