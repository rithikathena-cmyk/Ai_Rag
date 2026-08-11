import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.auth.jwt import TokenError, create_access_token, create_refresh_token, decode_token
from app.services.auth.password import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.query(UserModel).filter(UserModel.email == email).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Incorrect email or password")
    if not user.is_active:
        raise AppError(403, "user_inactive", "This account has been deactivated")

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id, user.role),
    )


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise AppError(401, "invalid_token", str(exc))

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AppError(401, "invalid_token", "Token is missing a valid subject claim")

    user = db.get(UserModel, user_id)
    if user is None or not user.is_active:
        raise AppError(401, "invalid_token", "Token refers to a user that no longer exists or is inactive")

    return AccessTokenResponse(access_token=create_access_token(user.id, user.role))


@router.get("/me", response_model=CurrentUserResponse)
def me(user: UserModel = Depends(get_current_user)):
    return CurrentUserResponse(
        id=user.id, email=user.email, display_name=user.display_name,
        role=user.role, is_active=user.is_active, created_at=user.created_at,
    )
