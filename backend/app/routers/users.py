import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.user import UserModel
from app.services.auth.password import hash_password
from app.services.memory.preferences import get_preferences, update_preferences

router = APIRouter()


class UserCreateRequest(BaseModel):
    email: str
    display_name: str | None = None
    password: str


class UserUpdateRequest(BaseModel):
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool
    role: str
    created_at: datetime


def _to_response(row: UserModel) -> UserResponse:
    return UserResponse(
        id=row.id, email=row.email, display_name=row.display_name,
        is_active=row.is_active, role=row.role, created_at=row.created_at,
    )


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(body: UserCreateRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.query(UserModel).filter(UserModel.email == email).one_or_none():
        raise AppError(409, "email_already_registered", f"A user with email {email} already exists")
    row = UserModel(email=email, display_name=body.display_name, password_hash=hash_password(body.password))
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return _to_response(row)


@router.get("/users", response_model=list[UserResponse])
def list_users(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    rows = db.query(UserModel).order_by(UserModel.created_at.desc()).offset(offset).limit(limit).all()
    return [_to_response(r) for r in rows]


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: uuid.UUID, body: UserUpdateRequest, db: Session = Depends(get_db)):
    row = db.get(UserModel, user_id)
    if row is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    if body.role is not None:
        row.role = body.role
    if body.is_active is not None:
        row.is_active = body.is_active
    db.commit()
    db.refresh(row)
    return _to_response(row)


@router.get("/users/{user_id}/preferences", response_model=dict)
def get_user_preferences(user_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.get(UserModel, user_id) is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return get_preferences(db, user_id)


@router.put("/users/{user_id}/preferences", response_model=dict)
def put_user_preferences(user_id: uuid.UUID, body: dict, db: Session = Depends(get_db)):
    if db.get(UserModel, user_id) is None:
        raise AppError(404, "user_not_found", f"User {user_id} not found")
    return update_preferences(db, user_id, body)
