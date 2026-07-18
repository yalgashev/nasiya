from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import (
    RawSessionToken,
    hash_session_token,
    resolve_by_raw_token,
    touch_session,
)
from app.settings import Settings

LOGIN_PATH: Final = "/login"


class CurrentSessionStatus(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"
    INVALID = "invalid"
    REVOKED = "revoked"
    EXPIRED = "expired"
    INACTIVE_USER = "inactive_user"


@dataclass(frozen=True, repr=False)
class CurrentSessionContext:
    status: CurrentSessionStatus
    session_id: UUID | None = None
    user_id: UUID | None = None
    _session: AuthSession | None = field(default=None, repr=False, compare=False)
    _user: User | None = field(default=None, repr=False, compare=False)

    @property
    def is_authenticated(self) -> bool:
        return self.status == CurrentSessionStatus.AUTHENTICATED

    def get_authenticated_user(self) -> User | None:
        return self._user

    def get_session_row(self) -> AuthSession | None:
        return self._session

    def __repr__(self) -> str:
        return (
            "CurrentSessionContext("
            f"status={self.status!s}, session_id={self.session_id}, "
            f"user_id={self.user_id}"
            ")"
        )


class LoginRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Login required",
            headers={"Location": LOGIN_PATH},
        )


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database_session(
    request: Request,
) -> Generator[DatabaseSession, None, None]:
    yield from request.app.state.get_database_session()


def get_current_time() -> datetime:
    return datetime.now(UTC)


def get_current_session_context(
    request: Request,
    db: Annotated[DatabaseSession, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_current_time)],
) -> CurrentSessionContext:
    cookie_value = request.cookies.get(settings.session_cookie_name)
    current_time = _as_utc(now)
    if cookie_value is None:
        return CurrentSessionContext(status=CurrentSessionStatus.ANONYMOUS)

    try:
        raw_token = RawSessionToken(cookie_value)
    except ValueError:
        return CurrentSessionContext(status=CurrentSessionStatus.INVALID)

    resolved = resolve_by_raw_token(db, raw_token, current_time)
    if resolved is None:
        return _get_unresolved_session_context(db, raw_token, current_time)
    if resolved.authenticated_user is None:
        return CurrentSessionContext(
            status=CurrentSessionStatus.ANONYMOUS,
            session_id=resolved.session.id,
            _session=resolved.session,
        )

    touch_session(db, resolved.session, current_time, settings=settings)
    return CurrentSessionContext(
        status=CurrentSessionStatus.AUTHENTICATED,
        session_id=resolved.session.id,
        user_id=resolved.authenticated_user.id,
        _session=resolved.session,
        _user=resolved.authenticated_user,
    )


def require_user(
    context: Annotated[
        CurrentSessionContext,
        Depends(get_current_session_context),
    ],
) -> User:
    user = context.get_authenticated_user()
    if user is None:
        raise LoginRequired()
    return user


def _get_unresolved_session_context(
    db: DatabaseSession,
    raw_token: RawSessionToken,
    now: datetime,
) -> CurrentSessionContext:
    statement = select(AuthSession).where(
        AuthSession.token_hash == hash_session_token(raw_token)
    )
    session = db.scalar(statement)
    if session is None:
        return CurrentSessionContext(status=CurrentSessionStatus.INVALID)
    if session.revoked_at is not None:
        return _failed_context(CurrentSessionStatus.REVOKED, session)
    if _as_utc(session.expires_at) <= now:
        return _failed_context(CurrentSessionStatus.EXPIRED, session)
    if session.user_id is not None:
        user = db.get(User, session.user_id)
        if user is None or not user.is_active:
            return _failed_context(CurrentSessionStatus.INACTIVE_USER, session)
    return CurrentSessionContext(status=CurrentSessionStatus.INVALID)


def _failed_context(
    context_status: CurrentSessionStatus,
    session: AuthSession,
) -> CurrentSessionContext:
    return CurrentSessionContext(
        status=context_status,
        session_id=session.id,
        user_id=session.user_id,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Session timestamps must be timezone-aware")
    return value.astimezone(UTC)
