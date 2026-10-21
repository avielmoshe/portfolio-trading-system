from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.order import OrderRequest, OrderResponse
from app.services import asset as asset_service
from app.services import position as position_service
from app.services import pricing as pricing_service
from app.services import trade as trade_service
from app.services import user as user_service

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Execute a BUY or SELL order at the server-resolved market price",
    description=(
        "The price is decided by the server: the live Redis tick when fresh, otherwise the latest OHLCV "
        "close. The request carries no price field and sending one is rejected. Call GET /api/assets/{symbol}/quote first to "
        "show the user what they will pay. Executes atomically: cash, position and ledger entry "
        "all move in one transaction, or none of them do."
    ),
    responses={
        400: {"description": "Insufficient funds or shares"},
        404: {"description": "Unknown symbol"},
        409: {"description": "Asset not tradable, or no usable market price"},
    },
)
def create_order(body: OrderRequest, current_user: CurrentUser, db: DbSession) -> OrderResponse:
    # Resolve the asset and its price BEFORE the trade takes any row lock: both are reads of
    # reference data, and doing them later would hold the user's lock during validation.
    try:
        asset = asset_service.resolve_tradable(db, body.symbol)
    except asset_service.AssetNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown symbol")
    except asset_service.AssetNotTradableError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset is not tradable")
    try:
        resolved = pricing_service.resolve_execution_price(db, asset)
    except pricing_service.PriceUnavailableError as exc:
        # Refusing the trade is recoverable; filling it at a guessed price is not.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    execute = trade_service.buy if body.action == "BUY" else trade_service.sell
    try:
        result = execute(db, current_user.id, body.symbol, body.quantity, resolved.price)
    except user_service.InsufficientFundsError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient funds")
    except position_service.InsufficientSharesError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient shares")
    # One commit for the whole trade; the services only flushed.
    db.commit()
    # Re-read with the asset joined: the response nests it, and the model forbids a lazy load.
    position = position_service.get_position(db, current_user.id, result.asset.id)
    return OrderResponse(
        action=body.action,
        asset=result.asset,
        quantity=body.quantity,
        price_per_share=resolved.price,
        total=result.total,
        cash_balance=result.user.cash_balance,
        price_as_of=resolved.as_of,
        price_source=resolved.source,
        price_interval=resolved.interval,
        position=position,
        transaction=result.entry,
    )
