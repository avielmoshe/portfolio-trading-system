from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_and_update_password
from app.models.user import User
from app.services.user import get_user_by_email


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Return the user when email+password match, otherwise None (never says which part failed)."""
    user = get_user_by_email(db, email)
    is_valid, new_hash = verify_and_update_password(password, user.hashed_password if user else None)
    if user is None or not is_valid:
        return None
    if new_hash is not None:
        # Transparent upgrade of an old-parameter hash; the caller's commit persists it.
        user.hashed_password = new_hash
    return user
