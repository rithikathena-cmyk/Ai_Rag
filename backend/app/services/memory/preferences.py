import uuid

from sqlalchemy.orm import Session

from app.models.user import UserModel


def get_preferences(db: Session, user_id: uuid.UUID) -> dict:
    user = db.get(UserModel, user_id)
    if user is None:
        return {}
    return user.preferences or {}


def update_preferences(db: Session, user_id: uuid.UUID, updates: dict) -> dict:
    user = db.get(UserModel, user_id)
    if user is None:
        return {}
    user.preferences = {**(user.preferences or {}), **updates}
    db.commit()
    db.refresh(user)
    return user.preferences or {}
