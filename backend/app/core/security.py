import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

# Argon2 is memory-hard, so brute-forcing a leaked hash is expensive on GPUs;
# "auto" lets passlib re-hash old hashes when the parameters are upgraded.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(minutes=15)


def _load_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    # Fail at startup, not on the first login: a short/missing secret makes every token forgeable.
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set in the environment (at least 32 characters)")
    return secret


_JWT_SECRET = _load_jwt_secret()

# Hash of a throwaway password, used to spend the same CPU time when the email doesn't exist.
_DUMMY_HASH = _pwd_context.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(password, hashed_password)


def verify_and_update_password(password: str, hashed_password: str | None) -> tuple[bool, str | None]:
    """Return (is_valid, new_hash_or_None).

    With no stored hash we still run a full Argon2 verify against a dummy hash, so a
    missing account takes as long as a wrong password and login timing can't be used
    to discover which emails are registered. new_hash is set when passlib says the
    stored hash uses outdated parameters (the only moment the plaintext is available
    to upgrade it).
    """
    if hashed_password is None:
        _pwd_context.verify(password, _DUMMY_HASH)
        return False, None
    return _pwd_context.verify_and_update(password, hashed_password)


def create_access_token(user_id: int) -> tuple[str, int]:
    """Return (jwt, expires_in_seconds)."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        # jti = unique token id; lets us revoke a single token later (Redis denylist).
        "jti": uuid.uuid4().hex,
        # token_type stops a future refresh token from being accepted as an access token.
        "token_type": "access",
        # Set to True only by the future OTP step; password-only logins are mfa=False.
        "mfa": False,
    }
    token = jwt.encode(claims, _JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, int(ACCESS_TOKEN_TTL.total_seconds())


def decode_access_token(token: str) -> int | None:
    """Return the user id from a valid access token, or None for any problem.

    The caller turns None into one flat 401, so the response never reveals whether
    the signature, the expiry, or the type was wrong.
    """
    try:
        # algorithms=[...] is an allow-list: a forged header with alg "none" is rejected.
        claims = jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
        if claims.get("token_type") != "access":
            return None
        return int(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        return None
