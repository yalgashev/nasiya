from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy.orm import Session as DatabaseSession

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.auth.models import User
from app.auth.rate_limit import AuthRateLimiter, RateLimitResult
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp

TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE: Final = "telegram_link_issue_user"
TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE: Final = "telegram_link_issue_phone"
TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE: Final = "telegram_link_issue_ip"

TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX: Final = "telegram_link_issue:user:"
TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX: Final = "telegram_link_issue:phone:"
TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX: Final = "telegram_link_issue:ip:"


@dataclass(frozen=True, repr=False)
class TelegramLinkIssuanceRateLimitResult:
    allowed: bool
    error_code: ErrorCode | None = None
    public_error: dict[str, str] | None = None

    def __repr__(self) -> str:
        return (
            "TelegramLinkIssuanceRateLimitResult("
            f"allowed={self.allowed}, error_code={self.error_code}"
            ")"
        )


@dataclass(frozen=True)
class _TelegramLinkRateLimitBucket:
    scope: str
    raw_key: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, repr=False)
class TelegramLinkIssuanceRateLimitPolicy:
    db: DatabaseSession
    settings: Settings

    def check(
        self,
        current_user: User,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> TelegramLinkIssuanceRateLimitResult:
        return check_telegram_link_issuance_rate_limit(
            self.db,
            self.settings,
            current_user,
            client_ip,
            now,
        )

    def record_attempt(
        self,
        current_user: User,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> TelegramLinkIssuanceRateLimitResult:
        return record_telegram_link_issuance_attempt(
            self.db,
            self.settings,
            current_user,
            client_ip,
            now,
        )


def check_telegram_link_issuance_rate_limit(
    db: DatabaseSession,
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> TelegramLinkIssuanceRateLimitResult:
    limiter = AuthRateLimiter(db=db, settings=settings)
    results = [
        limiter.check(
            bucket.scope,
            bucket.raw_key,
            now,
            bucket.limit,
            bucket.window_seconds,
        )
        for bucket in _build_rate_limit_buckets(settings, current_user, client_ip)
    ]
    return _result_from_bucket_results(results)


def record_telegram_link_issuance_attempt(
    db: DatabaseSession,
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> TelegramLinkIssuanceRateLimitResult:
    limiter = AuthRateLimiter(db=db, settings=settings)
    buckets = _build_rate_limit_buckets(settings, current_user, client_ip)
    check_results = [
        limiter.check(
            bucket.scope,
            bucket.raw_key,
            now,
            bucket.limit,
            bucket.window_seconds,
        )
        for bucket in buckets
    ]
    if _any_bucket_blocked(check_results):
        return _blocked()

    record_results = [
        limiter.record_failure(
            bucket.scope,
            bucket.raw_key,
            now,
            bucket.limit,
            bucket.window_seconds,
        )
        for bucket in buckets
    ]
    return _result_from_bucket_results(record_results)


def _build_rate_limit_buckets(
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
) -> tuple[_TelegramLinkRateLimitBucket, ...]:
    return (
        _TelegramLinkRateLimitBucket(
            scope=TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
            raw_key=f"{TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX}{current_user.id}",
            limit=_to_existing_limiter_threshold(
                settings.telegram_link_rate_limit_user_attempts
            ),
            window_seconds=settings.telegram_link_rate_limit_window_seconds,
        ),
        _TelegramLinkRateLimitBucket(
            scope=TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
            raw_key=f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{current_user.phone}",
            limit=_to_existing_limiter_threshold(
                settings.telegram_link_rate_limit_phone_attempts
            ),
            window_seconds=settings.telegram_link_rate_limit_window_seconds,
        ),
        _TelegramLinkRateLimitBucket(
            scope=TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
            raw_key=(
                f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}{client_ip.as_hmac_input()}"
            ),
            limit=_to_existing_limiter_threshold(
                settings.telegram_link_rate_limit_ip_attempts
            ),
            window_seconds=settings.telegram_link_rate_limit_window_seconds,
        ),
    )


def _to_existing_limiter_threshold(allowed_attempts: int) -> int:
    return allowed_attempts + 1


def _result_from_bucket_results(
    bucket_results: list[RateLimitResult],
) -> TelegramLinkIssuanceRateLimitResult:
    if _any_bucket_blocked(bucket_results):
        return _blocked()
    return TelegramLinkIssuanceRateLimitResult(allowed=True)


def _any_bucket_blocked(bucket_results: list[RateLimitResult]) -> bool:
    return any(not result.allowed for result in bucket_results)


def _blocked() -> TelegramLinkIssuanceRateLimitResult:
    return TelegramLinkIssuanceRateLimitResult(
        allowed=False,
        error_code=ErrorCode.RATE_LIMITED,
        public_error=get_public_error_body(
            ErrorCode.RATE_LIMITED,
            internal_detail="telegram link issuance rate limited",
        ),
    )
