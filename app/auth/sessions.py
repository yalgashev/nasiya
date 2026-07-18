from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.user_agent import get_user_agent_metadata, truncate_user_agent
from app.settings import Settings

SESSION_TOKEN_ENTROPY_BYTES: Final = 32
CSRF_SECRET_ENTROPY_BYTES: Final = 32


@dataclass(frozen=True, repr=False)
class RawSessionToken:
    _value: str

    def __post_init__(self) -> None:
        if not self._value:
            raise ValueError("Session token cannot be empty")

    def as_cookie_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "RawSessionToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-session-token>"


@dataclass(frozen=True, repr=False)
class CreatedSession:
    raw_token: RawSessionToken
    session: AuthSession

    def __repr__(self) -> str:
        return "CreatedSession(raw_token=<redacted>, session=<AuthSession>)"


@dataclass(frozen=True, repr=False)
class ResolvedSession:
    session: AuthSession
    authenticated_user: User | None

    def __repr__(self) -> str:
        return (
            "ResolvedSession("
            "session=<AuthSession>, authenticated_user=<User | None>"
            ")"
        )


class UserSessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class UserSessionSummary:
    session_id: UUID
    user_agent: str | None
    browser_label: str
    device_label: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    status: UserSessionStatus


def create_session_token() -> RawSessionToken:
    return RawSessionToken(secrets.token_urlsafe(SESSION_TOKEN_ENTROPY_BYTES))


def hash_session_token(raw_token: RawSessionToken | str) -> str:
    token_value = (
        raw_token.as_cookie_value()
        if isinstance(raw_token, RawSessionToken)
        else raw_token
    )
    return hashlib.sha256(token_value.encode("utf-8")).hexdigest()


def constant_time_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def create_anonymous_session(
    db: DatabaseSession,
    user_agent: str | None,
    now: datetime,
    settings: Settings | None = None,
) -> CreatedSession:
    resolved_settings = settings or Settings()
    return _create_session(
        db=db,
        user_id=None,
        user_agent=user_agent,
        now=now,
        expires_at=_as_utc(now)
        + timedelta(minutes=resolved_settings.anonymous_session_ttl_minutes),
    )


def create_authenticated_session(
    db: DatabaseSession,
    user_id: UUID,
    user_agent: str | None,
    now: datetime,
    settings: Settings | None = None,
) -> CreatedSession:
    resolved_settings = settings or Settings()
    return _create_session(
        db=db,
        user_id=user_id,
        user_agent=user_agent,
        now=now,
        expires_at=_as_utc(now) + timedelta(days=resolved_settings.session_ttl_days),
    )


def rotate_session(
    db: DatabaseSession,
    current_session: AuthSession | None,
    user_id: UUID,
    user_agent: str | None,
    now: datetime,
    settings: Settings | None = None,
) -> CreatedSession:
    if current_session is not None:
        revoke_session(db, current_session, now)
    return create_authenticated_session(
        db=db,
        user_id=user_id,
        user_agent=user_agent,
        now=now,
        settings=settings,
    )


def resolve_by_raw_token(
    db: DatabaseSession,
    raw_token: RawSessionToken | str,
    now: datetime,
) -> ResolvedSession | None:
    token_hash = hash_session_token(raw_token)
    statement = select(AuthSession).where(AuthSession.token_hash == token_hash)
    session = db.scalar(statement)
    if session is None:
        return None

    current_time = _as_utc(now)
    if session.revoked_at is not None:
        return None
    if _as_utc(session.expires_at) <= current_time:
        return None
    if session.user_id is None:
        return ResolvedSession(session=session, authenticated_user=None)

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return ResolvedSession(session=session, authenticated_user=user)


def revoke_session(
    db: DatabaseSession,
    session: AuthSession,
    now: datetime,
) -> None:
    session.revoked_at = _as_utc(now)
    db.add(session)


def touch_session(
    db: DatabaseSession,
    session: AuthSession,
    now: datetime,
    settings: Settings | None = None,
) -> bool:
    resolved_settings = settings or Settings()
    current_time = _as_utc(now)
    if not _is_active_authenticated_session(db, session, current_time):
        return False

    touch_interval = timedelta(
        minutes=resolved_settings.session_touch_interval_minutes
    )
    if _as_utc(session.last_seen_at) + touch_interval > current_time:
        return False

    session.last_seen_at = current_time
    session.expires_at = current_time + timedelta(
        days=resolved_settings.session_ttl_days
    )
    db.add(session)
    return True


def list_user_sessions(
    db: DatabaseSession,
    user_id: UUID,
    now: datetime,
) -> list[UserSessionSummary]:
    current_time = _as_utc(now)
    statement = (
        select(AuthSession)
        .where(AuthSession.user_id == user_id)
        .order_by(AuthSession.last_seen_at.desc(), AuthSession.created_at.desc())
    )
    return [
        _summarize_user_session(session, current_time)
        for session in db.scalars(statement)
    ]


def revoke_user_session(
    db: DatabaseSession,
    user_id: UUID,
    session_id: UUID,
    now: datetime,
) -> bool:
    session = _get_user_session(db, user_id, session_id)
    if session is None:
        return False
    if session.revoked_at is None:
        revoke_session(db, session, now)
    return True


def revoke_other_sessions(
    db: DatabaseSession,
    user_id: UUID,
    current_session_id: UUID,
    now: datetime,
) -> int:
    current_session = _get_user_session(db, user_id, current_session_id)
    if current_session is None:
        return 0

    statement = select(AuthSession).where(
        AuthSession.user_id == user_id,
        AuthSession.id != current_session_id,
        AuthSession.revoked_at.is_(None),
    )
    revoked_count = 0
    for session in db.scalars(statement):
        revoke_session(db, session, now)
        revoked_count += 1
    return revoked_count


def _create_session(
    db: DatabaseSession,
    user_id: UUID | None,
    user_agent: str | None,
    now: datetime,
    expires_at: datetime,
) -> CreatedSession:
    raw_token = create_session_token()
    session = AuthSession(
        user_id=user_id,
        token_hash=hash_session_token(raw_token),
        csrf_secret=_create_csrf_secret(),
        user_agent=_normalize_user_agent(user_agent),
        created_at=_as_utc(now),
        last_seen_at=_as_utc(now),
        expires_at=_as_utc(expires_at),
        revoked_at=None,
    )
    db.add(session)
    return CreatedSession(raw_token=raw_token, session=session)


def _create_csrf_secret() -> str:
    return secrets.token_urlsafe(CSRF_SECRET_ENTROPY_BYTES)


def _normalize_user_agent(user_agent: str | None) -> str | None:
    return truncate_user_agent(user_agent)


def _is_active_authenticated_session(
    db: DatabaseSession,
    session: AuthSession,
    now: datetime,
) -> bool:
    if session.user_id is None:
        return False
    if session.revoked_at is not None:
        return False
    if _as_utc(session.expires_at) <= now:
        return False

    user = db.get(User, session.user_id)
    return user is not None and user.is_active


def _get_user_session(
    db: DatabaseSession,
    user_id: UUID,
    session_id: UUID,
) -> AuthSession | None:
    statement = select(AuthSession).where(
        AuthSession.id == session_id,
        AuthSession.user_id == user_id,
    )
    return db.scalar(statement)


def _summarize_user_session(
    session: AuthSession,
    now: datetime,
) -> UserSessionSummary:
    user_agent_metadata = get_user_agent_metadata(session.user_agent)
    return UserSessionSummary(
        session_id=session.id,
        user_agent=user_agent_metadata.raw_user_agent,
        browser_label=user_agent_metadata.browser_label,
        device_label=user_agent_metadata.device_label,
        created_at=_as_utc(session.created_at),
        last_seen_at=_as_utc(session.last_seen_at),
        expires_at=_as_utc(session.expires_at),
        revoked_at=_as_utc(session.revoked_at) if session.revoked_at else None,
        status=_get_user_session_status(session, now),
    )


def _get_user_session_status(
    session: AuthSession,
    now: datetime,
) -> UserSessionStatus:
    if session.revoked_at is not None:
        return UserSessionStatus.REVOKED
    if _as_utc(session.expires_at) <= now:
        return UserSessionStatus.EXPIRED
    return UserSessionStatus.ACTIVE


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Session timestamps must be timezone-aware")
    return value.astimezone(UTC)
