from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.transaction import TransactionAction
from app.schemas.position import AssetBrief

# Matches Numeric(18,8); the cap rejects absurd values before they reach SQL.
Quantity = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), max_digits=18, decimal_places=8)]
Price = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), max_digits=18, decimal_places=8)]

_SCALE = Decimal("0.00000001")  # matches Numeric(18, 8)


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: TransactionAction
    # None for DEPOSIT / WITHDRAW, which move cash and involve no instrument.
    asset: AssetBrief | None
    quantity: Decimal
    price_per_share: Decimal
    timestamp: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> Decimal:
        # Derived, never stored: a stored copy could drift from the two numbers it comes from.
        # Quantized to the column scale, since Decimal multiplication adds the two scales
        # together and would otherwise report 16 decimal places.
        return (self.quantity * self.price_per_share).quantize(_SCALE, rounding=ROUND_HALF_UP)


class TransactionPage(BaseModel):
    items: list[TransactionResponse]
    # Opaque token for the next page; null means this was the last one.
    next_cursor: str | None
