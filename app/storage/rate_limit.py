"""Storage upload attempt policy over the existing HMAC rate limiter."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.auth.rate_limit import AuthRateLimiter, RateLimitResult
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp

STORAGE_UPLOAD_USER_SCOPE: Final = "storage_upload_user"
STORAGE_UPLOAD_IP_SCOPE: Final = "storage_upload_ip"
STORAGE_UPLOAD_USER_KEY_PREFIX: Final = "storage_upload:user:"
STORAGE_UPLOAD_IP_KEY_PREFIX: Final = "storage_upload:ip:"

__all__ = (
    "STORAGE_UPLOAD_IP_KEY_PREFIX",
    "STORAGE_UPLOAD_IP_SCOPE",
    "STORAGE_UPLOAD_USER_KEY_PREFIX",
    "STORAGE_UPLOAD_USER_SCOPE",
    "StorageUploadRateLimitPolicy",
    "StorageUploadRateLimitResult",
    "check_storage_upload_rate_limit",
    "record_storage_upload_attempt",
)


@dataclass(frozen=True, repr=False)
class StorageUploadRateLimitResult:
    allowed: bool
    error_code: ErrorCode | None = None
    public_error: dict[str, str] | None = None

    def __repr__(self) -> str:
        return (
            "StorageUploadRateLimitResult("
            f"allowed={self.allowed!r}, error_code={self.error_code!r}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class StorageUploadRateLimitPolicy:
    db: Session
    settings: Settings

    def __repr__(self) -> str:
        return "StorageUploadRateLimitPolicy(db=<redacted>, settings=<redacted>)"

    def check(
        self,
        actor_user_id: UUID,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> StorageUploadRateLimitResult:
        return check_storage_upload_rate_limit(
            self.db,
            self.settings,
            actor_user_id,
            client_ip,
            now,
        )

    def record_attempt(
        self,
        actor_user_id: UUID,
        client_ip: ResolvedClientIp,
        now: datetime,
    ) -> StorageUploadRateLimitResult:
        return record_storage_upload_attempt(
            self.db,
            self.settings,
            actor_user_id,
            client_ip,
            now,
        )


@dataclass(frozen=True, repr=False)
class _StorageUploadRateLimitBucket:
    scope: str
    raw_key: str
    limit: int
    window_seconds: int

    def __repr__(self) -> str:
        return (
            "_StorageUploadRateLimitBucket("
            f"scope={self.scope!r}, raw_key=<redacted>, "
            f"limit={self.limit!r}, window_seconds={self.window_seconds!r}"
            ")"
        )


def check_storage_upload_rate_limit(
    db: Session,
    settings: Settings,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> StorageUploadRateLimitResult:
    limiter = AuthRateLimiter(db=db, settings=settings)
    results = [
        limiter.check(
            bucket.scope,
            bucket.raw_key,
            now,
            bucket.limit,
            bucket.window_seconds,
        )
        for bucket in _build_buckets(settings, actor_user_id, client_ip)
    ]
    return _result_from_buckets(results)


def record_storage_upload_attempt(
    db: Session,
    settings: Settings,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> StorageUploadRateLimitResult:
    limiter = AuthRateLimiter(db=db, settings=settings)
    buckets = _build_buckets(settings, actor_user_id, client_ip)
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
    if _any_blocked(check_results):
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
    return _result_from_buckets(record_results)


def _build_buckets(
    settings: Settings,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
) -> tuple[_StorageUploadRateLimitBucket, _StorageUploadRateLimitBucket]:
    if not isinstance(actor_user_id, UUID) or actor_user_id.int == 0:
        raise ValueError("Storage upload actor must be a non-zero UUID")
    if not isinstance(client_ip, ResolvedClientIp):
        raise TypeError("Storage upload client IP must be resolved")

    return (
        _StorageUploadRateLimitBucket(
            scope=STORAGE_UPLOAD_USER_SCOPE,
            raw_key=f"{STORAGE_UPLOAD_USER_KEY_PREFIX}{actor_user_id}",
            limit=_to_existing_limiter_threshold(
                settings.object_storage_upload_rate_limit_user_attempts
            ),
            window_seconds=(settings.object_storage_upload_rate_limit_window_seconds),
        ),
        _StorageUploadRateLimitBucket(
            scope=STORAGE_UPLOAD_IP_SCOPE,
            raw_key=(f"{STORAGE_UPLOAD_IP_KEY_PREFIX}{client_ip.as_hmac_input()}"),
            limit=_to_existing_limiter_threshold(
                settings.object_storage_upload_rate_limit_ip_attempts
            ),
            window_seconds=(settings.object_storage_upload_rate_limit_window_seconds),
        ),
    )


def _to_existing_limiter_threshold(allowed_attempts: int) -> int:
    return allowed_attempts + 1


def _result_from_buckets(
    bucket_results: list[RateLimitResult],
) -> StorageUploadRateLimitResult:
    if _any_blocked(bucket_results):
        return _blocked()
    return StorageUploadRateLimitResult(allowed=True)


def _any_blocked(bucket_results: list[RateLimitResult]) -> bool:
    return any(not result.allowed for result in bucket_results)


def _blocked() -> StorageUploadRateLimitResult:
    return StorageUploadRateLimitResult(
        allowed=False,
        error_code=ErrorCode.RATE_LIMITED,
        public_error=get_public_error_body(
            ErrorCode.RATE_LIMITED,
            internal_detail="storage upload rate limited",
        ),
    )
