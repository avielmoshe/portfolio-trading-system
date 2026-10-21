from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AssetBrief(BaseModel):
    """Just enough asset detail to render a holdings row without a second request."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    company_name: str


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Nested rather than a flattened `symbol`: positions store only asset_id, and copying the
    # symbol onto the row would create a second source of truth that can drift.
    asset: AssetBrief
    total_quantity: Decimal
    average_buy_price: Decimal
