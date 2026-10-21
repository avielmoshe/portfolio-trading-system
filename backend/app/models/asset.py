from sqlalchemy import Boolean, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Asset(Base):
    """Static reference data for one tradable instrument: the single source of truth for
    which symbols exist. Rows are deactivated, never deleted, because positions and
    transactions point here with ON DELETE RESTRICT."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique index, not just a constraint: every trade resolves a symbol to an asset_id on the
    # hot path, so this lookup has to be an index seek rather than a table scan.
    symbol: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Text, not String(n): the profile-page blurb has no sensible length cap, and in Postgres
    # text and varchar are the same storage, so the limit would buy nothing.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-delete flag. A delisted symbol must stay readable for old positions and ledger rows,
    # so trading is blocked by is_active=False instead of removing the row.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
