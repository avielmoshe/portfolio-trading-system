from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.position import Position
from app.models.transaction import Transaction, TransactionAction
from app.models.user import User
from app.services import asset as asset_service
from app.services import position as position_service
from app.services import transaction as transaction_service
from app.services import user as user_service

_SCALE = Decimal("0.00000001")


@dataclass(frozen=True)
class TradeResult:
    user: User
    asset: Asset
    position: Position
    entry: Transaction
    total: Decimal  # cash that left (buy) or entered (sell) the account


def _total(quantity: Decimal, price: Decimal) -> Decimal:
    # Rounded once to the column scale so the cash moved is exactly what Postgres will store.
    return (quantity * price).quantize(_SCALE, rounding=ROUND_HALF_UP)


# A trade touches three rows: the user's cash, the user's position in that asset, and one
# append-only ledger entry. Like the other services these never commit: the caller's single
# commit makes all three succeed or fail together, so the ledger can never disagree with the
# balance it explains. If anything raises, the caller rolls back and nothing changes.
#
# The symbol is resolved to an asset BEFORE any lock is taken: it is an unlocked read of static
# data, and resolving it later would mean holding the user's row lock while validating input.
# resolve_tradable raises on an unknown or delisted symbol, so neither can reach a write.
#
# Locks are ALWAYS taken in the same order: user row first, then position row. If one trade
# locked position-then-user while another locked user-then-position, each would wait for the
# other forever (deadlock); a fixed order makes that impossible.


def buy(db: Session, user_id: int, symbol: str, quantity: Decimal, price: Decimal) -> TradeResult:
    if quantity <= 0 or price <= 0:
        raise ValueError("quantity and price must be > 0")
    asset = asset_service.resolve_tradable(db, symbol)
    cost = _total(quantity, price)
    user = user_service.lock_user(db, user_id)
    # Checked under the user's row lock, so two concurrent buys can't both spend the same cash.
    # (The CHECK cash_balance >= 0 in the DB is the last line of defence, not the first.)
    if user.cash_balance < cost:
        raise user_service.InsufficientFundsError
    user.cash_balance -= cost
    position = position_service.apply_buy(db, user_id, asset.id, quantity, price)
    entry = transaction_service.record(
        db, user_id, TransactionAction.BUY, quantity, price, asset_id=asset.id
    )
    return TradeResult(user=user, asset=asset, position=position, entry=entry, total=cost)


def sell(db: Session, user_id: int, symbol: str, quantity: Decimal, price: Decimal) -> TradeResult:
    if quantity <= 0 or price <= 0:
        raise ValueError("quantity and price must be > 0")
    asset = asset_service.resolve_tradable(db, symbol)
    proceeds = _total(quantity, price)
    user = user_service.lock_user(db, user_id)
    # Raises InsufficientSharesError before any cash is credited.
    position = position_service.apply_sell(db, user_id, asset.id, quantity)
    user.cash_balance += proceeds
    entry = transaction_service.record(
        db, user_id, TransactionAction.SELL, quantity, price, asset_id=asset.id
    )
    return TradeResult(user=user, asset=asset, position=position, entry=entry, total=proceeds)
