from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models.transaction import TransactionAction
from app.schemas.transaction import TransactionPage
from app.services import asset as asset_service
from app.services import transaction as transaction_service

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.get(
    "",
    response_model=TransactionPage,
    summary="My transaction ledger, newest first",
    description=(
        "Keyset pagination: pass the returned `next_cursor` as `cursor` to get the next page. "
        "A null `next_cursor` means there are no more rows."
    ),
    responses={400: {"description": "Malformed cursor"}, 404: {"description": "Unknown symbol"}},
)
def list_transactions(
    current_user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page")] = None,
    action: TransactionAction | None = None,
    symbol: Annotated[str | None, Query(description="Filter to one asset")] = None,
) -> TransactionPage:
    asset_id = None
    if symbol is not None:
        asset = asset_service.get_asset_by_symbol(db, symbol)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown symbol")
        asset_id = asset.id
    try:
        # Scoped to the token's user: the ledger is queried by user_id, never from a stored
        # list of transaction ids on the user row.
        items, next_cursor = transaction_service.list_for_user(
            db, current_user.id, limit, cursor, action, asset_id
        )
    except transaction_service.InvalidCursorError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed cursor")
    return TransactionPage(items=items, next_cursor=next_cursor)
