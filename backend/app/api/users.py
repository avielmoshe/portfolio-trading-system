from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.schemas.user import MoneyRequest, UserCreate, UserResponse, UserUpdate
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/createUser", response_model=UserResponse, responses={400: {"description": "Email already registered"}})
def create_user(user: UserCreate, db: DbSession) -> UserResponse:
    try:
        new_user = user_service.create_user(db, user.email, user.password)
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(status_code=400, detail="Email already registered")
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/me", response_model=UserResponse, summary="Get my profile")
def read_me(current_user: CurrentUser) -> UserResponse:
    return current_user


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update my email and/or password",
    responses={400: {"description": "Email already registered"}, 403: {"description": "Wrong current password"}},
)
def update_me(changes: UserUpdate, current_user: CurrentUser, db: DbSession) -> UserResponse:
    try:
        user = user_service.update_user(
            db, current_user, changes.email, changes.new_password, changes.current_password
        )
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(status_code=400, detail="Email already registered")
    except user_service.WrongPasswordError:
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    db.commit()
    db.refresh(user)
    return user


@router.post("/me/deposit", response_model=UserResponse, summary="Add cash to my balance")
def deposit(body: MoneyRequest, current_user: CurrentUser, db: DbSession) -> UserResponse:
    # The user id comes from the token, never from the request body.
    user = user_service.deposit(db, current_user.id, body.amount)
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/me/withdraw",
    response_model=UserResponse,
    summary="Withdraw cash from my balance",
    responses={400: {"description": "Insufficient funds"}},
)
def withdraw(body: MoneyRequest, current_user: CurrentUser, db: DbSession) -> UserResponse:
    try:
        user = user_service.withdraw(db, current_user.id, body.amount)
    except user_service.InsufficientFundsError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient funds")
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=list[UserResponse], summary="List all users (admin only)")
def list_users(
    _admin: AdminUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserResponse]:
    return user_service.list_users(db, limit, offset)
