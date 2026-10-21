from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.asset import Asset


class SymbolAlreadyExistsError(Exception):
    pass


class AssetNotFoundError(Exception):
    pass


class AssetNotTradableError(Exception):
    pass


# Like every service here, these never commit: the caller owns the transaction.


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def create_asset(
    db: Session,
    symbol: str,
    company_name: str,
    sector: str | None = None,
    description: str | None = None,
    is_active: bool = True,
) -> Asset:
    asset = Asset(
        symbol=normalize_symbol(symbol),
        company_name=company_name,
        sector=sector,
        description=description,
        is_active=is_active,
    )
    db.add(asset)
    try:
        # The UNIQUE index decides, not a prior SELECT: two concurrent creates of the same
        # ticker would both pass a check-then-insert, but only one can win the constraint.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise SymbolAlreadyExistsError(symbol) from exc
    return asset


def get_asset(db: Session, asset_id: int) -> Asset | None:
    return db.get(Asset, asset_id)


def get_asset_by_symbol(db: Session, symbol: str) -> Asset | None:
    return db.scalar(select(Asset).where(Asset.symbol == normalize_symbol(symbol)))


def resolve_tradable(db: Session, symbol: str) -> Asset:
    """Symbol -> Asset for the trade path. Raises instead of returning None, so a typo or a
    delisted ticker can never reach the position/ledger write."""
    asset = get_asset_by_symbol(db, symbol)
    if asset is None:
        raise AssetNotFoundError(symbol)
    if not asset.is_active:
        raise AssetNotTradableError(symbol)
    return asset


def list_assets(
    db: Session,
    limit: int,
    offset: int,
    active_only: bool = True,
    sector: str | None = None,
    search: str | None = None,
) -> list[Asset]:
    stmt = select(Asset)
    if active_only:
        stmt = stmt.where(Asset.is_active.is_(True))
    if sector is not None:
        stmt = stmt.where(Asset.sector == sector)
    if search is not None:
        # ilike on both columns powers the watchlist search box. Fine at this catalogue size;
        # if it grows, this is the query to replace with a Postgres full-text index.
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(Asset.symbol.ilike(pattern) | Asset.company_name.ilike(pattern))
    # Stable ORDER BY: LIMIT/OFFSET without one can repeat or skip rows between pages.
    return list(db.scalars(stmt.order_by(Asset.symbol).limit(limit).offset(offset)))


def update_asset(
    db: Session,
    asset: Asset,
    changes: dict[str, object],
) -> Asset:
    """Apply only the keys present in `changes` (PATCH). `symbol` is not updatable: positions
    and ledger rows reference this asset by id, and renaming it would rewrite their meaning."""
    for field, value in changes.items():
        if field == "symbol":
            continue
        setattr(asset, field, value)
    db.flush()
    return asset


def set_active(db: Session, asset: Asset, is_active: bool) -> Asset:
    # Soft delete / restore. There is deliberately no delete_asset(): dropping a row would
    # orphan every position and transaction that references it.
    asset.is_active = is_active
    db.flush()
    return asset
