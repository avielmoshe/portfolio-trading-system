from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.historical_price import CandleInterval
from app.schemas.asset import Symbol
from app.schemas.position import AssetBrief, PositionResponse
from app.schemas.transaction import Quantity, TransactionResponse


class OrderRequest(BaseModel):
    """What the client may state about a trade: the instrument, the side, and how much.

    Deliberately NOT the price. The execution price is resolved server-side
    (`services/pricing.resolve_execution_price`); accepting it from the client let a caller
    buy at 0.01. `extra="forbid"` means a request that still sends `price_per_share` is
    rejected with 422 rather than silently ignored, so an outdated client fails loudly.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Symbol
    action: Literal["BUY", "SELL"]
    quantity: Quantity


class OrderResponse(BaseModel):
    """What the trade did: the price the server chose, the ledger entry, the resulting holding,
    and the new cash balance."""

    action: Literal["BUY", "SELL"]
    asset: AssetBrief
    quantity: Decimal
    price_per_share: Decimal
    total: Decimal
    cash_balance: Decimal
    # Provenance of the price, so a filled order can be audited against the candle it used.
    price_as_of: datetime
    price_source: Literal["redis", "candle"]
    # null when the price came from a live tick, which has no aggregation interval
    price_interval: CandleInterval | None
    position: PositionResponse
    transaction: TransactionResponse
