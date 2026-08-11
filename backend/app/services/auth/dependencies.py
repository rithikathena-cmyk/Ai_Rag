import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.user import UserModel
from app.services.auth.jwt import TokenError, decode_token

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> UserModel:
    if credentials is None:
        raise AppError(401, "not_authenticated", "Missing bearer token")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise AppError(401, "invalid_token", str(exc))

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AppError(401, "invalid_token", "Token is missing a valid subject claim")

    user = db.get(UserModel, user_id)
    if user is None:
        raise AppError(401, "invalid_token", "Token refers to a user that no longer exists")
    if not user.is_active:
        raise AppError(403, "user_inactive", "This account has been deactivated")

    return user
