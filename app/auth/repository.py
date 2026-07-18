from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User


def get_by_phone(session: Session, normalized_phone: str) -> User | None:
    statement = select(User).where(User.phone == normalized_phone)
    return session.execute(statement).scalar_one_or_none()


def add_user(session: Session, user: User) -> User:
    session.add(user)
    return user
