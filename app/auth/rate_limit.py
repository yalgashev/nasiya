import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit
from app.settings import Settings


@dataclass(frozen=True, repr=False)
class RateLimitResult:
    allowed: bool
    error_code: ErrorCode | None = None
    attempts_remaining: int = 0
    retry_after_seconds: int | None = None

    def __repr__(self) -> str:
        return (
            "RateLimitResult("
            f"allowed={self.allowed}, error_code={self.error_code}, "
            f"attempts_remaining={self.attempts_remaining}, "
            f"retry_after_seconds={self.retry_after_seconds}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class AuthRateLimiter:
    db: DatabaseSession
    settings: Settings

    def check(
        self,
        scope: str,
        raw_key: str,
        now: datetime,
        limit: int,
        window_seconds: int,
    ) -> RateLimitResult:
        return check(
            self.db,
            self.settings,
            scope,
            raw_key,
            now,
            limit,
            window_seconds,
        )

    def record_failure(
        self,
        scope: str,
        raw_key: str,
        now: datetime,
        limit: int,
        window_seconds: int,
    ) -> RateLimitResult:
        return record_failure(
            self.db,
            self.settings,
            scope,
            raw_key,
            now,
            limit,
            window_seconds,
        )

    def clear_key(self, scope: str, raw_key: str) -> bool:
        return clear_key(self.db, self.settings, scope, raw_key)


def check(
    db: DatabaseSession,
    settings: Settings,
    scope: str,
    raw_key: str,
    now: datetime,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    current_time = _as_utc(now)
    _validate_limit(limit)
    _validate_window_seconds(window_seconds)
    normalized_scope = _normalize_scope(scope)
    key_hash = hash_rate_limit_key(settings, raw_key)

    statement = (
        select(AuthRateLimit)
        .where(
            AuthRateLimit.scope == normalized_scope,
            AuthRateLimit.key_hash == key_hash,
        )
        .with_for_update()
    )
    record = db.scalar(statement)
    if record is None:
        return _allowed(limit)
    if _window_expired(record.window_started_at, current_time, window_seconds):
        return _allowed(limit)
    return _result_from_attempts(
        attempt_count=record.attempt_count,
        window_started_at=record.window_started_at,
        now=current_time,
        limit=limit,
        window_seconds=window_seconds,
    )


def record_failure(
    db: DatabaseSession,
    settings: Settings,
    scope: str,
    raw_key: str,
    now: datetime,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    current_time = _as_utc(now)
    _validate_limit(limit)
    _validate_window_seconds(window_seconds)
    normalized_scope = _normalize_scope(scope)
    key_hash = hash_rate_limit_key(settings, raw_key)
    reset_cutoff = current_time - timedelta(seconds=window_seconds)

    insert_statement = insert(AuthRateLimit).values(
        scope=normalized_scope,
        key_hash=key_hash,
        window_started_at=current_time,
        attempt_count=1,
        updated_at=current_time,
    )
    statement = (
        insert_statement.on_conflict_do_update(
            index_elements=[AuthRateLimit.scope, AuthRateLimit.key_hash],
            set_={
                "window_started_at": case(
                    (
                        AuthRateLimit.window_started_at <= reset_cutoff,
                        current_time,
                    ),
                    else_=AuthRateLimit.window_started_at,
                ),
                "attempt_count": case(
                    (AuthRateLimit.window_started_at <= reset_cutoff, 1),
                    else_=AuthRateLimit.attempt_count + 1,
                ),
                "updated_at": current_time,
            },
        )
        .returning(
            AuthRateLimit.attempt_count,
            AuthRateLimit.window_started_at,
        )
    )
    attempt_count, window_started_at = db.execute(statement).one()
    return _result_from_attempts(
        attempt_count=attempt_count,
        window_started_at=window_started_at,
        now=current_time,
        limit=limit,
        window_seconds=window_seconds,
    )


def clear_key(
    db: DatabaseSession,
    settings: Settings,
    scope: str,
    raw_key: str,
) -> bool:
    normalized_scope = _normalize_scope(scope)
    key_hash = hash_rate_limit_key(settings, raw_key)
    statement = delete(AuthRateLimit).where(
        AuthRateLimit.scope == normalized_scope,
        AuthRateLimit.key_hash == key_hash,
    )
    result = db.execute(statement)
    return result.rowcount is not None and result.rowcount > 0


def hash_rate_limit_key(settings: Settings, raw_key: str) -> str:
    if not raw_key:
        raise ValueError("rate limit key must not be empty")

    secret = settings.rate_limit_hmac_key.get_secret_value()
    return hmac.new(
        secret.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _result_from_attempts(
    attempt_count: int,
    window_started_at: datetime,
    now: datetime,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    remaining = max(limit - attempt_count, 0)
    if attempt_count < limit:
        return RateLimitResult(allowed=True, attempts_remaining=remaining)

    retry_after = _retry_after_seconds(window_started_at, now, window_seconds)
    return RateLimitResult(
        allowed=False,
        error_code=ErrorCode.RATE_LIMITED,
        attempts_remaining=0,
        retry_after_seconds=retry_after,
    )


def _allowed(limit: int) -> RateLimitResult:
    return RateLimitResult(allowed=True, attempts_remaining=limit)


def _retry_after_seconds(
    window_started_at: datetime,
    now: datetime,
    window_seconds: int,
) -> int:
    window_ends_at = _as_utc(window_started_at) + timedelta(seconds=window_seconds)
    retry_after = window_ends_at - now
    return max(int(retry_after.total_seconds()), 0)


def _window_expired(
    window_started_at: datetime,
    now: datetime,
    window_seconds: int,
) -> bool:
    return _as_utc(window_started_at) + timedelta(seconds=window_seconds) <= now


def _normalize_scope(scope: str) -> str:
    normalized_scope = scope.strip()
    if not normalized_scope:
        raise ValueError("rate limit scope must not be empty")
    if len(normalized_scope) > 64:
        raise ValueError("rate limit scope must not be longer than 64 characters")
    return normalized_scope


def _validate_limit(limit: int) -> None:
    if limit <= 0:
        raise ValueError("rate limit must be positive")


def _validate_window_seconds(window_seconds: int) -> None:
    if window_seconds <= 0:
        raise ValueError("rate limit window must be positive")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("rate limit timestamps must be timezone-aware")
    return value.astimezone(UTC)
