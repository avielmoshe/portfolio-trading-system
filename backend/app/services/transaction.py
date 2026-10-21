import base64
import binascii
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, select, tuple_
from sqlalchemy.orm import Session, joinedload

from app.models.transaction import Transaction, TransactionAction

_ONE = Decimal("1")


class InvalidCursorError(Exception):
    pass


# There is deliberately no update() or delete() here, and the database enforces that with a
# trigger. A correction is a new compensating row, because an audit trail you can edit is not
# an audit trail. Like every service, record() never commits: the caller's single commit makes
# the ledger row land in the same transaction as the cash and position changes it describes.


def record(
    db: Session,
    user_id: int,
    action: TransactionAction,
    quantity: Decimal,
    price_per_share: Decimal,
    asset_id: int | None = None,
) -> Transaction:
    entry = Transaction(
        user_id=user_id,
        asset_id=asset_id,
        action=action,
        quantity=quantity,
        price_per_share=price_per_share,
    )
    db.add(entry)
    db.flush()
    return entry


def record_cash(db: Session, user_id: int, action: TransactionAction, amount: Decimal) -> Transaction:
    """DEPOSIT / WITHDRAW. The amount goes in `quantity` with price 1, so the invariant
    `total = quantity * price_per_share` holds for every row in the ledger."""
    return record(db, user_id, action, quantity=amount, price_per_share=_ONE, asset_id=None)


def encode_cursor(entry: Transaction) -> str:
    # Opaque on purpose: clients must treat it as a token, so the sort key can change later
    # without breaking their saved cursors.
    raw = f"{entry.timestamp.isoformat()}|{entry.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        timestamp_text, _, id_text = raw.rpartition("|")
        return datetime.fromisoformat(timestamp_text), int(id_text)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError(cursor) from exc


def list_for_user(
    db: Session,
    user_id: int,
    limit: int,
    cursor: str | None = None,
    action: TransactionAction | None = None,
    asset_id: int | None = None,
) -> tuple[list[Transaction], str | None]:
    """Newest-first page of one user's ledger, plus the cursor for the next page.

    Keyset, not OFFSET: the ledger only grows, and OFFSET makes the database walk and discard
    every skipped row, so page 500 gets linearly slower. Comparing the (timestamp, id) tuple
    seeks straight into ix_transactions_user_timestamp_id instead.
    """
    stmt: Select[tuple[Transaction]] = (
        select(Transaction)
        .options(joinedload(Transaction.asset))
        .where(Transaction.user_id == user_id)
        # id breaks ties: two rows can share a timestamp, and without it a page boundary
        # could repeat or skip one of them.
        .order_by(Transaction.timestamp.desc(), Transaction.id.desc())
    )
    if action is not None:
        stmt = stmt.where(Transaction.action == action)
    if asset_id is not None:
        stmt = stmt.where(Transaction.asset_id == asset_id)
    if cursor is not None:
        last_timestamp, last_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Transaction.timestamp, Transaction.id) < (last_timestamp, last_id))

    # Fetch one extra row to learn whether a further page exists, without a COUNT(*).
    rows = list(db.scalars(stmt.limit(limit + 1)).unique())
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, encode_cursor(page[-1]) if has_more and page else None
