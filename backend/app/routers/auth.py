import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.request_context import get_current_request_id
from app.db.postgres import get_db
from app.models.user import UserModel
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.auth.dependencies import get_current_user
from app.services.auth.jwt import TokenError, create_access_token, create_refresh_token, decode_token
from app.services.auth.password import verify_password
from app.services.llm_rbac import policy_loader

router = APIRouter(prefix="/auth", tags=["auth"])

# Demo tile id -> real llm_rbac.yaml role key. A closed set, never a
# client-supplied email/id — this mapping (plus the Literal on
# DemoLoginRequest below) is the actual security boundary that keeps
# /auth/demo-login from being usable to authenticate as an arbitrary account.
_DEMO_ROLE_MAP: dict[str, str] = {
    "employee": "user",
    "hr": "hr",
    "project_manager": "project_manager",
    "ceo": "ceo",
    "admin": "admin",
}
# Honest one-line description per demo tile — reflects this deployment's
# actual seeded department config (see seed_users.py), not generic copy.
_DEMO_ROLE_DESCRIPTIONS: dict[str, str] = {
    "employee": "Manufacturing",
    "hr": "Human Resources",
    "project_manager": "Engineering",
    "ceo": "Executive",
    "admin": "Privileged Access",
}


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


class DemoUserTile(BaseModel):
    demo_role: str
    display_name: str
    description: str
    is_privileged: bool
    # Not sensitive — every seeded demo account's email is already visible in
    # the org-wide Traces view for privileged roles, and this is exactly what
    # gets typed into the login form's email field for the tile-fill effect
    # (frontend/pages/LoginPage.tsx). The password field is filled with a
    # decorative placeholder only — the real credential never leaves the
    # server; actual auth still goes through POST /auth/demo-login below.
    email: str


class DemoUsersResponse(BaseModel):
    enabled: bool
    users: list[DemoUserTile]


class DemoLoginRequest(BaseModel):
    demo_role: Literal["employee", "hr", "project_manager", "ceo", "admin"]


def _demo_account_for(db: Session, role: str) -> UserModel | None:
    """Lowest-numbered active seeded user for `role` (e.g. hr1@mail.com before
    hr2@mail.com) — deterministic and independent of insertion order."""
    return (
        db.query(UserModel)
        .filter(UserModel.role == role, UserModel.is_active.is_(True))
        .order_by(UserModel.email)
        .first()
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.query(UserModel).filter(UserModel.email == email).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        # actor_id is only ever set when the email matched a real account
        # (wrong password) — not for an email that doesn't exist at all, so
        # this event never becomes a place to enumerate which emails have
        # accounts. The raw attempted email itself is never stored either
        # way (see this module's audit-wiring comment below).
        audit_logger.log(
            AuditEventType.LOGIN_FAILURE, outcome=AuditOutcome.FAILURE, request_id=get_current_request_id(),
            actor_id=user.id if user is not None else None, actor_role=user.role if user is not None else None,
            resource_type="AUTH", action="LOGIN", reason_code="invalid_credentials",
        )
        raise AppError(401, "invalid_credentials", "Incorrect email or password")
    if not user.is_active:
        audit_logger.log(
            AuditEventType.LOGIN_FAILURE, outcome=AuditOutcome.FAILURE, request_id=get_current_request_id(),
            actor_id=user.id, actor_role=user.role, resource_type="AUTH", action="LOGIN",
            reason_code="user_inactive",
        )
        raise AppError(403, "user_inactive", "This account has been deactivated")

    audit_logger.log(
        AuditEventType.LOGIN_SUCCESS, outcome=AuditOutcome.SUCCESS, request_id=get_current_request_id(),
        actor_id=user.id, actor_role=user.role, resource_type="AUTH", action="LOGIN",
    )
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


@router.get("/demo-users", response_model=DemoUsersResponse)
def list_demo_users(db: Session = Depends(get_db)):
    if not settings.demo_login_enabled:
        return DemoUsersResponse(enabled=False, users=[])

    tiles: list[DemoUserTile] = []
    for demo_role, role in _DEMO_ROLE_MAP.items():
        account = _demo_account_for(db, role)
        if account is None:
            # No seeded account for this role in this deployment — the tile
            # list stays dynamic rather than always showing all five.
            continue
        tiles.append(
            DemoUserTile(
                demo_role=demo_role,
                display_name=policy_loader.role_config(role).display_name,
                description=_DEMO_ROLE_DESCRIPTIONS.get(demo_role, role),
                is_privileged=demo_role == "admin",
                email=account.email,
            )
        )
    return DemoUsersResponse(enabled=True, users=tiles)


@router.post("/demo-login", response_model=TokenResponse)
def demo_login(body: DemoLoginRequest, db: Session = Depends(get_db)):
    if not settings.demo_login_enabled:
        raise AppError(404, "demo_login_disabled", "Demo login is not available")

    role = _DEMO_ROLE_MAP[body.demo_role]
    user = _demo_account_for(db, role)
    if user is None:
        raise AppError(404, "demo_account_missing", "No demo account is configured for this role")

    # Same call shape as normal login's success path — only the reason_code
    # distinguishes how the session started; every downstream consumer
    # (get_current_user, require_permission, guardrails, audit logging of
    # subsequent actions) reads user.id/user.role fresh from the DB and has
    # no notion of a separate "demo" auth scheme.
    audit_logger.log(
        AuditEventType.LOGIN_SUCCESS, outcome=AuditOutcome.SUCCESS, request_id=get_current_request_id(),
        actor_id=user.id, actor_role=user.role, resource_type="AUTH", action="LOGIN",
        reason_code="demo_login",
    )
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id, user.role),
    )
