from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.auth.models import User
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLinkToken
from app.telegram.rate_limit import record_telegram_link_issuance_attempt
from app.telegram.repository import (
    TelegramLinkTokenInsertConflict,
    has_active_telegram_link,
    invalidate_and_insert_telegram_link_token,
)
from app.telegram.token import (
    RawTelegramLinkToken,
    create_telegram_link_token,
    hash_telegram_link_token,
)

TELEGRAM_LINK_TOKEN_TTL_SECONDS: Final = 600


@dataclass(frozen=True, repr=False)
class IssuedTelegramLinkToken:
    raw_token: RawTelegramLinkToken
    token: TelegramLinkToken

    def __repr__(self) -> str:
        return (
            "IssuedTelegramLinkToken("
            "raw_token=<redacted>, token=<TelegramLinkToken>"
            ")"
        )


class TelegramLinkTokenIssueError(RuntimeError):
    def __init__(
        self,
        error_code: ErrorCode,
        public_error: dict[str, str] | None = None,
    ) -> None:
        self.error_code = error_code
        self.public_error = public_error or get_public_error_body(
            error_code,
            internal_detail="telegram link token issue failed",
        )
        super().__init__(error_code.value)


class TelegramLinkTokenIssueInternalError(RuntimeError):
    pass


def issue_link_token(
    session: DatabaseSession,
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
    token_generator: Callable[[int], str] | None = None,
) -> IssuedTelegramLinkToken:
    current_time = _as_utc(now)
    try:
        canonical_user = _get_canonical_current_user(session, current_user)

        rate_limit_result = record_telegram_link_issuance_attempt(
            session,
            settings,
            canonical_user,
            client_ip,
            current_time,
        )
        if not rate_limit_result.allowed:
            raise TelegramLinkTokenIssueError(
                rate_limit_result.error_code or ErrorCode.RATE_LIMITED,
                public_error=rate_limit_result.public_error,
            )

        if has_active_telegram_link(session, canonical_user):
            raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_ALREADY_LINKED)

        raw_token = create_telegram_link_token(token_generator)
        token_hash = hash_telegram_link_token(raw_token)
        expires_at = current_time + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS)
        token = invalidate_and_insert_telegram_link_token(
            session,
            canonical_user,
            token_hash,
            current_time,
            expires_at,
        )
    except TelegramLinkTokenInsertConflict:
        raise TelegramLinkTokenIssueError(
            ErrorCode.RATE_LIMITED,
            public_error=get_public_error_body(
                ErrorCode.RATE_LIMITED,
                internal_detail="telegram link token insert conflict",
            ),
        ) from None
    except TelegramLinkTokenIssueError:
        raise
    except SQLAlchemyError:
        raise TelegramLinkTokenIssueInternalError(
            "Telegram link token issue failed"
        ) from None

    return IssuedTelegramLinkToken(raw_token=raw_token, token=token)


def _get_canonical_current_user(
    session: DatabaseSession,
    current_user: User,
) -> User:
    user = session.get(User, current_user.id)
    if user is None or not user.is_active:
        raise TelegramLinkTokenIssueError(ErrorCode.UNAUTHORIZED)
    return user


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Telegram link token issue timestamp must be timezone-aware")
    return value.astimezone(UTC)
