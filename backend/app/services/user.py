from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.transaction import TransactionAction
from app.models.user import User
from app.services import transaction as transaction_service


class EmailAlreadyExistsError(Exception):
    pass


class InsufficientFundsError(Exception):
    pass


class WrongPasswordError(Exception):
    pass


# These helpers never commit: the caller (route / unit of work) owns the transaction,
# so several operations can later be combined into one atomic commit.


def _flush_unique_email(db: Session, email: str) -> None:
    try:
        # flush sends the INSERT/UPDATE now (so the DB assigns id/created_at and checks UNIQUE)
        # without ending the transaction. We rely on the UNIQUE index instead of
        # "SELECT then INSERT": that check-then-insert has a race window where two
        # concurrent requests both pass the check; the constraint is atomic.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise EmailAlreadyExistsError(email) from exc


def create_user(db: Session, email: str, password: str) -> User:
    user = User(email=email, hashed_password=hash_password(password))
    db.add(user)
    _flush_unique_email(db, email)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def list_users(db: Session, limit: int, offset: int) -> list[User]:
    # Stable ORDER BY id: LIMIT/OFFSET without an ordering can return rows in a different order per call.
    return list(db.scalars(select(User).order_by(User.id).limit(limit).offset(offset)))


def update_user(
    db: Session,
    user: User,
    email: str | None,
    new_password: str | None,
    current_password: str | None,
) -> User:
    if new_password is not None:
        if current_password is None or not verify_password(current_password, user.hashed_password):
            raise WrongPasswordError
        user.hashed_password = hash_password(new_password)
    if email is not None:
        user.email = email
    _flush_unique_email(db, user.email)
    return user


def lock_user(db: Session, user_id: int) -> User:
    # FOR UPDATE: concurrent deposits/withdrawals on this user queue up behind this row lock,
    # so each one reads the balance the previous one committed (no lost update, no overdraft).
    # populate_existing: the user was already loaded by the auth dependency in this session;
    # without it SQLAlchemy would hand back that stale copy instead of the freshly locked row.
    stmt = select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    return db.execute(stmt).scalar_one()


def deposit(db: Session, user_id: int, amount: Decimal) -> User:
    user = lock_user(db, user_id)
    user.cash_balance += amount
    # Ledger row in the same transaction as the balance change, so the audit trail can never
    # be missing a movement that the balance already reflects.
    transaction_service.record_cash(db, user_id, TransactionAction.DEPOSIT, amount)
    db.flush()
    return user


def withdraw(db: Session, user_id: int, amount: Decimal) -> User:
    user = lock_user(db, user_id)
    if user.cash_balance < amount:
        raise InsufficientFundsError
    user.cash_balance -= amount
    transaction_service.record_cash(db, user_id, TransactionAction.WITHDRAW, amount)
    db.flush()
    return user
