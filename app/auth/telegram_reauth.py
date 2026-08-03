from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.rate_limit import AuthRateLimiter, RateLimitResult
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp

TELEGRAM_REAUTH_WINDOW_SECONDS: Final = 900
TELEGRAM_REAUTH_USER_ATTEMPTS: Final = 5
TELEGRAM_REAUTH_IP_ATTEMPTS: Final = 20
TELEGRAM_REAUTH_USER_SCOPE: Final = "telegram_account_reauth_user"
TELEGRAM_REAUTH_IP_SCOPE: Final = "telegram_account_reauth_ip"
_TELEGRAM_REAUTH_USER_KEY_PREFIX: Final = "telegram_account_reauth:user:"
_TELEGRAM_REAUTH_IP_KEY_PREFIX: Final = "telegram_account_reauth:ip:"


@dataclass(frozen=True, repr=False)
class TelegramReauthRateLimitResult:
    allowed: bool
    error_code: ErrorCode | None = None

    def __repr__(self) -> str:
        return (
            "TelegramReauthRateLimitResult("
            f"allowed={self.allowed}, error_code={self.error_code})"
        )


@dataclass(frozen=True, repr=False)
class TelegramReauthRateLimitPolicy:
    db: Session
    settings: Settings

    def check(
        self,
        current_user: User,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> TelegramReauthRateLimitResult:
        return self.check_for_user_id(current_user.id, client_ip, now)

    def check_for_user_id(
        self,
        user_id: UUID,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> TelegramReauthRateLimitResult:
        limiter = AuthRateLimiter(self.db, self.settings)
        return _from_results(
            tuple(
                limiter.check(
                    scope,
                    raw_key,
                    now,
                    limit,
                    TELEGRAM_REAUTH_WINDOW_SECONDS,
                )
                for scope, raw_key, limit in _buckets_for_user_id(
                    user_id,
                    client_ip,
                )
            )
        )

    def record_failure(
        self,
        current_user: User,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> TelegramReauthRateLimitResult:
        return self.record_failure_for_user_id(current_user.id, client_ip, now)

    def record_failure_for_user_id(
        self,
        user_id: UUID,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> TelegramReauthRateLimitResult:
        limiter = AuthRateLimiter(self.db, self.settings)
        buckets = _buckets_for_user_id(user_id, client_ip)
        checked = tuple(
            limiter.check(
                scope,
                raw_key,
                now,
                limit,
                TELEGRAM_REAUTH_WINDOW_SECONDS,
            )
            for scope, raw_key, limit in buckets
        )
        if not _from_results(checked).allowed:
            return _blocked()

        recorded = tuple(
            limiter.record_failure(
                scope,
                raw_key,
                now,
                limit,
                TELEGRAM_REAUTH_WINDOW_SECONDS,
            )
            for scope, raw_key, limit in buckets
        )
        return _from_results(recorded)

    def clear_user_failures_after_success(self, current_user: User) -> bool:
        return self.clear_user_failures_after_success_for_user_id(current_user.id)

    def clear_user_failures_after_success_for_user_id(self, user_id: UUID) -> bool:
        limiter = AuthRateLimiter(self.db, self.settings)
        return limiter.clear_key(
            TELEGRAM_REAUTH_USER_SCOPE,
            _user_key(user_id),
        )


def _buckets_for_user_id(
    user_id: UUID,
    client_ip: ResolvedClientIp,
) -> tuple[tuple[str, str, int], ...]:
    return (
        (
            TELEGRAM_REAUTH_USER_SCOPE,
            _user_key(user_id),
            TELEGRAM_REAUTH_USER_ATTEMPTS,
        ),
        (
            TELEGRAM_REAUTH_IP_SCOPE,
            f"{_TELEGRAM_REAUTH_IP_KEY_PREFIX}{client_ip.as_hmac_input()}",
            TELEGRAM_REAUTH_IP_ATTEMPTS,
        ),
    )


def _user_key(user_id: UUID) -> str:
    return f"{_TELEGRAM_REAUTH_USER_KEY_PREFIX}{user_id}"


def _from_results(
    results: tuple[RateLimitResult, ...],
) -> TelegramReauthRateLimitResult:
    if all(result.allowed for result in results):
        return TelegramReauthRateLimitResult(allowed=True)
    return _blocked()


def _blocked() -> TelegramReauthRateLimitResult:
    return TelegramReauthRateLimitResult(
        allowed=False,
        error_code=ErrorCode.RATE_LIMITED,
    )
