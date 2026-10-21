from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# Matches Numeric(18,8) in the DB; the upper cap rejects absurd amounts before they reach SQL.
MoneyAmount = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), max_digits=18, decimal_places=8)]


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=128)
    # Required with new_password, so a stolen access token alone can't take over the account.
    current_password: str | None = None

    @model_validator(mode="after")
    def check_fields(self) -> Self:
        if self.email is None and self.new_password is None:
            raise ValueError("Provide email and/or new_password")
        if self.new_password is not None and self.current_password is None:
            raise ValueError("current_password is required to change the password")
        return self


class MoneyRequest(BaseModel):
    amount: MoneyAmount


class UserResponse(BaseModel):
    # from_attributes lets Pydantic read fields straight off the SQLAlchemy object.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    cash_balance: Decimal
    is_admin: bool
    created_at: datetime
