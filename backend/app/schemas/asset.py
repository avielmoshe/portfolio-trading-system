from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Tickers are short and uppercase; the pattern rejects junk before it reaches the DB.
Symbol = Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")]


class AssetCreate(BaseModel):
    symbol: Symbol
    company_name: Annotated[str, Field(min_length=1, max_length=255)]
    sector: Annotated[str | None, Field(default=None, max_length=64)] = None
    description: str | None = None
    is_active: bool = True

    @field_validator("symbol")
    @classmethod
    def upper(cls, value: str) -> str:
        # Normalised at the edge so "aapl" and "AAPL" can never become two assets.
        return value.strip().upper()


class AssetUpdate(BaseModel):
    """Every field optional: PATCH semantics. `symbol` is deliberately absent — renaming a
    ticker would silently rewrite the meaning of existing positions and ledger rows."""

    company_name: Annotated[str | None, Field(default=None, min_length=1, max_length=255)] = None
    sector: Annotated[str | None, Field(default=None, max_length=64)] = None
    description: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    company_name: str
    sector: str | None
    description: str | None
    is_active: bool
