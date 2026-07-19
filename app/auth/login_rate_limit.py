from dataclasses import dataclass
from datetime import datetime
from typing import Final

from fastapi import Request
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.auth.rate_limit import AuthRateLimiter, RateLimitResult
from app.settings import Settings

LOGIN_PHONE_SCOPE: Final = "login_phone"
LOGIN_IP_SCOPE: Final = "login_ip"


@dataclass(frozen=True, repr=False)
class LoginRateLimitResult:
    allowed: bool
    error_code: ErrorCode | None = None
    public_error: dict[str, str] | None = None

    def __repr__(self) -> str:
        return (
            "LoginRateLimitResult("
            f"allowed={self.allowed}, error_code={self.error_code}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class LoginRateLimitPolicy:
    db: DatabaseSession
    settings: Settings

    def check(
        self,
        phone_input: str,
        client_host: str,
        now: datetime,
    ) -> LoginRateLimitResult:
        return check_login_rate_limit(
            self.db,
            self.settings,
            phone_input,
            client_host,
            now,
        )

    def record_failure(
        self,
        phone_input: str,
        client_host: str,
        now: datetime,
    ) -> LoginRateLimitResult:
        return record_login_failure(
            self.db,
            self.settings,
            phone_input,
            client_host,
            now,
        )

    def clear_phone_failures_after_success(self, phone_input: str) -> bool:
        return clear_login_phone_failures(self.db, self.settings, phone_input)


def get_login_client_host(request: Request) -> str:
    client = request.client
    if client is None or not client.host.strip():
        raise ValueError("login client host is required")
    return client.host


def check_login_rate_limit(
    db: DatabaseSession,
    settings: Settings,
    phone_input: str,
    client_host: str,
    now: datetime,
) -> LoginRateLimitResult:
    limiter = AuthRateLimiter(db=db, settings=settings)
    ip_result = limiter.check(
        LOGIN_IP_SCOPE,
        _normalize_client_host(client_host),
        now,
        settings.login_rate_limit_ip_attempts,
        settings.login_rate_limit_window_seconds,
    )
    if not ip_result.allowed:
        return _blocked()

    normalized_phone = _normalize_phone_or_none(phone_input)
    if normalized_phone is None:
        return _allowed()

    phone_result = limiter.check(
        LOGIN_PHONE_SCOPE,
        normalized_phone,
        now,
        settings.login_rate_limit_phone_attempts,
        settings.login_rate_limit_window_seconds,
    )
    return _from_rate_limit_result(phone_result)


def record_login_failure(
    db: DatabaseSession,
    settings: Settings,
    phone_input: str,
    client_host: str,
    now: datetime,
) -> LoginRateLimitResult:
    limiter = AuthRateLimiter(db=db, settings=settings)
    ip_result = limiter.record_failure(
        LOGIN_IP_SCOPE,
        _normalize_client_host(client_host),
        now,
        settings.login_rate_limit_ip_attempts,
        settings.login_rate_limit_window_seconds,
    )

    normalized_phone = _normalize_phone_or_none(phone_input)
    if normalized_phone is None:
        return _from_rate_limit_result(ip_result)

    phone_result = limiter.record_failure(
        LOGIN_PHONE_SCOPE,
        normalized_phone,
        now,
        settings.login_rate_limit_phone_attempts,
        settings.login_rate_limit_window_seconds,
    )
    if not ip_result.allowed or not phone_result.allowed:
        return _blocked()
    return _allowed()


def clear_login_phone_failures(
    db: DatabaseSession,
    settings: Settings,
    phone_input: str,
) -> bool:
    normalized_phone = _normalize_phone_or_none(phone_input)
    if normalized_phone is None:
        return False

    limiter = AuthRateLimiter(db=db, settings=settings)
    return limiter.clear_key(LOGIN_PHONE_SCOPE, normalized_phone)


def _from_rate_limit_result(result: RateLimitResult) -> LoginRateLimitResult:
    if result.allowed:
        return _allowed()
    return _blocked()


def _allowed() -> LoginRateLimitResult:
    return LoginRateLimitResult(allowed=True)


def _blocked() -> LoginRateLimitResult:
    return LoginRateLimitResult(
        allowed=False,
        error_code=ErrorCode.RATE_LIMITED,
        public_error=get_public_error_body(
            ErrorCode.RATE_LIMITED,
            internal_detail="login rate limited",
        ),
    )


def _normalize_phone_or_none(phone_input: str) -> str | None:
    try:
        return normalize_uzbekistan_phone(phone_input)
    except PhoneNormalizationError:
        return None


def _normalize_client_host(client_host: str) -> str:
    normalized_host = client_host.strip()
    if not normalized_host:
        raise ValueError("login client host is required")
    return normalized_host
