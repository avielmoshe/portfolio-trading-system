from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class Position(Base):
    """Current holding of one asset for one user. A row is never deleted: selling everything
    leaves total_quantity = 0, so transaction rows that reference it stay valid."""

    __tablename__ = "positions"
    __table_args__ = (
        # One row per user per asset. The unique index also serves "all positions of user X"
        # and "position of user X in asset Y", and makes concurrent first-buys safe (see service).
        UniqueConstraint("user_id", "asset_id", name="uq_positions_user_asset"),
        CheckConstraint("total_quantity >= 0", name="ck_positions_quantity_non_negative"),
        CheckConstraint("average_buy_price >= 0", name="ck_positions_avg_price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: deleting a user or an asset must not silently erase holdings or break the ledger.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # Indexed separately from the (user_id, asset_id) unique index, which can't serve a lookup
    # by asset alone ("who holds AAPL?").
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    total_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    average_buy_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))

    # lazy="raise_on_sql": reading position.asset works when it was eager-loaded, but raises
    # instead of silently firing one SELECT per row. That turns an N+1 into a loud test failure
    # rather than a slow endpoint nobody notices.
    asset: Mapped[Asset] = relationship(lazy="raise_on_sql")
