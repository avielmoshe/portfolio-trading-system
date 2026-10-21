from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Numeric, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    # The DB itself rejects a negative balance, even if application code has a bug.
    __table_args__ = (CheckConstraint("cash_balance >= 0", name="ck_users_cash_balance_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    # Numeric (exact decimal) instead of Float: binary floats can't represent 0.1 exactly,
    # which silently loses money over many trades.
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("10000"))
    # Admin rights live in the DB, not in the token, so revoking admin takes effect immediately.
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # server_default: Postgres stamps the time itself (one clock, timezone-aware),
    # so rows inserted outside this app still get a correct value.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
