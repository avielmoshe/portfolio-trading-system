from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models.asset import Asset
from app.models.historical_price import CandleInterval
from app.schemas.asset import AssetCreate, AssetResponse, AssetUpdate
from app.schemas.historical_price import CandleImport, CandleSeries, ImportResult, QuoteResponse
from app.services import asset as asset_service
from app.services import historical_price as candle_service
from app.services import pricing as pricing_service

router = APIRouter(prefix="/assets", tags=["Assets"])


def _get_or_404(db: Session, symbol: str) -> Asset:
    found = asset_service.get_asset_by_symbol(db, symbol)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return found


def _as_utc(value: datetime | None) -> datetime | None:
    # A naive datetime compared against a timestamptz column is ambiguous across DST, so an
    # offset-less input is read as UTC rather than silently taking the server's local zone.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@router.get("", response_model=list[AssetResponse], summary="Browse / search the asset catalogue")
def list_assets(
    _user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sector: str | None = None,
    search: Annotated[str | None, Query(max_length=64, description="Matches symbol or company name")] = None,
    include_inactive: bool = False,
) -> list[AssetResponse]:
    return asset_service.list_assets(db, limit, offset, not include_inactive, sector, search)


@router.get(
    "/{symbol}",
    response_model=AssetResponse,
    summary="Asset profile page (includes description)",
    responses={404: {"description": "Asset not found"}},
)
def get_asset(symbol: str, _user: CurrentUser, db: DbSession) -> AssetResponse:
    return _get_or_404(db, symbol)


@router.post(
    "",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an asset to the catalogue (admin only)",
    responses={409: {"description": "Symbol already exists"}},
)
def create_asset(body: AssetCreate, _admin: AdminUser, db: DbSession) -> AssetResponse:
    try:
        asset = asset_service.create_asset(
            db, body.symbol, body.company_name, body.sector, body.description, body.is_active
        )
    except asset_service.SymbolAlreadyExistsError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Symbol already exists")
    db.commit()
    db.refresh(asset)
    return asset


@router.patch(
    "/{symbol}",
    response_model=AssetResponse,
    summary="Edit an asset's details or deactivate it (admin only)",
    responses={404: {"description": "Asset not found"}},
)
def update_asset(symbol: str, body: AssetUpdate, _admin: AdminUser, db: DbSession) -> AssetResponse:
    asset = _get_or_404(db, symbol)
    # exclude_unset: only the keys the client actually sent, so omitting a field leaves it alone
    # instead of overwriting it with None.
    asset_service.update_asset(db, asset, body.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(asset)
    return asset


@router.get(
    "/{symbol}/prices",
    response_model=CandleSeries,
    summary="Aggregated OHLCV candles for charting (Layer 1 of the dual-layer chart)",
    description=(
        "Returns candles in chronological order. These are server-aggregated candles, not raw "
        "ticks — live ticks arrive over the WebSocket and render only on the chart's active edge."
    ),
    responses={404: {"description": "Asset not found"}},
)
def get_prices(
    symbol: str,
    _user: CurrentUser,
    db: DbSession,
    interval: CandleInterval = CandleInterval.ONE_DAY,
    start: Annotated[datetime | None, Query(alias="from", description="ISO timestamp, inclusive")] = None,
    end: Annotated[datetime | None, Query(alias="to", description="ISO timestamp, inclusive")] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> CandleSeries:
    asset = _get_or_404(db, symbol)
    # Queried newest-first so LIMIT walks the DESC index and stops; reversed here because a
    # chart draws left-to-right in time.
    rows = candle_service.list_candles(db, asset.id, interval, _as_utc(start), _as_utc(end), limit)
    return CandleSeries(symbol=asset.symbol, interval=interval, candles=list(reversed(rows)))


@router.post(
    "/{symbol}/prices",
    response_model=ImportResult,
    summary="Bulk import / refresh candles (admin only)",
    description="Idempotent: re-importing a range upserts each candle instead of duplicating it.",
    responses={404: {"description": "Asset not found"}},
)
def import_prices(symbol: str, body: CandleImport, _admin: AdminUser, db: DbSession) -> ImportResult:
    asset = _get_or_404(db, symbol)
    candles = [
        {
            "timestamp": _as_utc(candle.timestamp),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in body.candles
    ]
    written = candle_service.upsert_candles(db, asset.id, body.interval, candles)
    db.commit()
    return ImportResult(symbol=asset.symbol, interval=body.interval, rows_written=written)


@router.get(
    "/{symbol}/quote",
    response_model=QuoteResponse,
    summary="The server-resolved price an order would execute at",
    description="Same resolution the order endpoint uses, so the UI can show the price before confirming.",
    responses={404: {"description": "Asset not found"}, 409: {"description": "No usable price"}},
)
def get_quote(symbol: str, _user: CurrentUser, db: DbSession) -> QuoteResponse:
    asset = _get_or_404(db, symbol)
    try:
        resolved = pricing_service.resolve_execution_price(db, asset)
    except pricing_service.PriceUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return QuoteResponse(
        symbol=asset.symbol,
        price=resolved.price,
        as_of=resolved.as_of,
        source=resolved.source,
        interval=resolved.interval,
    )
