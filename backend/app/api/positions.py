from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.position import PositionResponse
from app.services import asset as asset_service
from app.services import position as position_service

router = APIRouter(prefix="/positions", tags=["Positions"])


@router.get("", response_model=list[PositionResponse], summary="My current holdings")
def list_positions(
    current_user: CurrentUser,
    db: DbSession,
    include_closed: bool = False,
) -> list[PositionResponse]:
    # Holdings are scoped by the token's user id, never by a client-supplied one.
    # include_closed surfaces fully-sold rows (quantity 0), which are kept for the ledger.
    return position_service.list_positions(db, current_user.id, include_closed)


@router.get(
    "/{symbol}",
    response_model=PositionResponse,
    summary="My holding in one asset",
    responses={404: {"description": "Asset or position not found"}},
)
def get_position(symbol: str, current_user: CurrentUser, db: DbSession) -> PositionResponse:
    asset = asset_service.get_asset_by_symbol(db, symbol)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    position = position_service.get_position(db, current_user.id, asset.id)
    if position is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No position in this asset")
    return position
