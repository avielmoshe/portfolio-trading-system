import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class TransactionAction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"


# Native Postgres enum, not a free-text column: the DB rejects a typo like 'BYU' outright,
# and the type is 4 bytes instead of a string per row.
ACTION_ENUM = Enum(TransactionAction, name="transaction_action", native_enum=True)

_ASSET_ACTIONS = "'BUY', 'SELL'"
_CASH_ACTIONS = "'DEPOSIT', 'WITHDRAW'"


class Transaction(Base):
    """Append-only ledger of every cash and asset movement. This table is the source of
    truth: `positions` is a derived running total that could be rebuilt by replaying these
    rows. UPDATE and DELETE are blocked by a database trigger, not just by convention."""

    __tablename__ = "transactions"
    __table_args__ = (
        # A trade must name an asset; a cash movement must not. Enforced here so neither a
        # code bug nor a manual INSERT can leave an unreadable ledger row.
        CheckConstraint(
            f"(action IN ({_ASSET_ACTIONS}) AND asset_id IS NOT NULL)"
            f" OR (action IN ({_CASH_ACTIONS}) AND asset_id IS NULL)",
            name="ck_transactions_asset_matches_action",
        ),
        CheckConstraint("quantity > 0", name="ck_transactions_quantity_positive"),
        CheckConstraint("price_per_share > 0", name="ck_transactions_price_positive"),
        # Exactly the order the history query reads in: newest first, id breaking ties.
        # Keyset pagination walks this index instead of counting past skipped rows.
        Index("ix_transactions_user_timestamp_id", "user_id", "timestamp", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    # Nullable: DEPOSIT and WITHDRAW move cash and involve no instrument.
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    action: Mapped[TransactionAction] = mapped_column(ACTION_ENUM)
    # For DEPOSIT/WITHDRAW the cash amount goes in `quantity` with price_per_share = 1, so the
    # invariant "total = quantity * price_per_share" holds for every row in the ledger.
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    price_per_share: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped[Asset | None] = relationship(lazy="raise_on_sql")
