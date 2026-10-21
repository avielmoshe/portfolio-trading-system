from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from app.models.position import Position

_SCALE = Decimal("0.00000001")  # 8 decimal places, matching Numeric(18, 8)
_ZERO = Decimal("0")


class InsufficientSharesError(Exception):
    pass


# Positions are keyed by asset_id, never by symbol: the ticker lives in `assets` only, so it
# can't drift between tables. Callers resolve a symbol first (asset_service.resolve_tradable).
#
# Like all services here, these never commit. They must run inside the caller's transaction
# (the same one that moves cash and appends the ledger row), so a trade is all-or-nothing.


def get_position(db: Session, user_id: int, asset_id: int) -> Position | None:
    return db.scalar(
        select(Position)
        .options(joinedload(Position.asset))
        .where(Position.user_id == user_id, Position.asset_id == asset_id)
    )


def list_positions(db: Session, user_id: int, include_closed: bool = False) -> list[Position]:
    stmt = (
        select(Position)
        # joinedload: one SQL join instead of an extra SELECT per holding (the model sets
        # lazy="raise_on_sql", so forgetting this raises instead of quietly going N+1).
        .options(joinedload(Position.asset))
        .join(Position.asset)
        .where(Position.user_id == user_id)
        .order_by(Position.asset_id)
    )
    if not include_closed:
        stmt = stmt.where(Position.total_quantity > 0)
    return list(db.scalars(stmt).unique())


def _lock_or_create(db: Session, user_id: int, asset_id: int) -> Position:
    # ON CONFLICT DO NOTHING makes "create if missing" a single atomic statement. A plain
    # "SELECT, then INSERT if empty" lets two concurrent first-buys both see nothing and
    # both insert; the loser would crash with a unique violation instead of waiting its turn.
    db.execute(
        pg_insert(Position)
        .values(user_id=user_id, asset_id=asset_id, total_quantity=_ZERO, average_buy_price=_ZERO)
        .on_conflict_do_nothing(constraint="uq_positions_user_asset")
    )
    # FOR UPDATE: the second concurrent trade on this (user, asset) blocks here until the first
    # commits, then reads the committed quantity/average, so no update is lost.
    # populate_existing: ignore any stale copy of the row already in this session.
    stmt = (
        select(Position)
        .where(Position.user_id == user_id, Position.asset_id == asset_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.execute(stmt).scalar_one()


def apply_buy(db: Session, user_id: int, asset_id: int, quantity: Decimal, price: Decimal) -> Position:
    """Add shares to the user's single row for this asset and recompute the weighted average cost."""
    if quantity <= 0 or price < 0:
        raise ValueError("quantity must be > 0 and price >= 0")
    position = _lock_or_create(db, user_id, asset_id)
    new_quantity = position.total_quantity + quantity
    # Weighted average: (old_qty*old_avg + new_qty*price) / (old_qty + new_qty). With old_qty = 0 this
    # reduces to the new price, so the first buy needs no special case. Decimal (not float) keeps the
    # arithmetic exact; the result is rounded once per buy, to the column's scale. Because the next buy
    # starts from that rounded average, the error is bounded (about 5e-9 per buy) but it DOES accumulate
    # slowly: measured 1.9e-8 after 37 buys. If sub-1e-7 cost-basis accuracy ever matters, store the
    # running total cost instead and derive the average from it (see test_average_price_drift...).
    total_cost = position.total_quantity * position.average_buy_price + quantity * price
    position.average_buy_price = (total_cost / new_quantity).quantize(_SCALE, rounding=ROUND_HALF_UP)
    position.total_quantity = new_quantity
    db.flush()
    return position


def apply_sell(db: Session, user_id: int, asset_id: int, quantity: Decimal) -> Position:
    """Remove shares. The average buy price is unchanged by a sale (it is the cost of what's still held)."""
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    position = _lock_or_create(db, user_id, asset_id)
    # Checked under the same row lock as the update, so two concurrent sells can't both pass
    # the check and oversell the holding.
    if position.total_quantity < quantity:
        raise InsufficientSharesError
    position.total_quantity -= quantity
    if position.total_quantity == 0:
        position.average_buy_price = _ZERO  # nothing held, so no cost basis; the row stays for the ledger
    db.flush()
    return position
