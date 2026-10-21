from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.historical_price import CandleInterval

Price = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), max_digits=18, decimal_places=8)]


class CandleIn(BaseModel):
    """One candle in an import batch. `timestamp` is the candle's OPEN time."""

    timestamp: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def check_ohlc(self) -> Self:
        # Mirrors the DB CHECK constraints so a bad batch fails with a readable 422 naming the
        # candle, instead of a raw IntegrityError after the round trip.
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.high < self.open or self.high < self.close:
            raise ValueError("high must be the maximum of open/high/low/close")
        if self.low > self.open or self.low > self.close:
            raise ValueError("low must be the minimum of open/high/low/close")
        return self


class CandleImport(BaseModel):
    interval: CandleInterval
    candles: Annotated[list[CandleIn], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def no_duplicate_timestamps(self) -> Self:
        # Postgres rejects a multi-row upsert that hits the same key twice ("cannot affect row a
        # second time"), so catch it here with a message that says which timestamp.
        seen = set()
        for candle in self.candles:
            if candle.timestamp in seen:
                raise ValueError(f"duplicate timestamp in batch: {candle.timestamp.isoformat()}")
            seen.add(candle.timestamp)
        return self


class CandleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class CandleSeries(BaseModel):
    """Chart payload: candles in chronological order, ready to hand to a charting library."""

    symbol: str
    interval: CandleInterval
    candles: list[CandleResponse]


class ImportResult(BaseModel):
    symbol: str
    interval: CandleInterval
    rows_written: int


class QuoteResponse(BaseModel):
    symbol: str
    price: Decimal
    as_of: datetime
    source: Literal["redis", "candle"]
    interval: CandleInterval | None
