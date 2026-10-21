from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.core.security import create_access_token
from app.schemas.auth import LoginRequest, TokenResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with email + password and receive a JWT access token",
    responses={401: {"description": "Invalid email or password"}},
)
def login(credentials: LoginRequest, db: DbSession) -> TokenResponse:
    user = auth_service.authenticate(db, credentials.email, credentials.password)
    if user is None:
        # One message for "no such email" and "wrong password".
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    db.commit()  # persists a transparent password-hash upgrade, if authenticate() made one
    token, expires_in = create_access_token(user.id)
    return TokenResponse(access_token=token, expires_in=expires_in)
