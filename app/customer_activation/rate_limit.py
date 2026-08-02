from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from app.auth.models import User
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.settings import RegistrationOtpConfig
from app.telegram.client_ip import ResolvedClientIp


class RegistrationRateLimitScope(StrEnum):
    PHONE = "otp-registration-issue:phone"
    USER = "otp-registration-issue:user"
    IP = "otp-registration-issue:ip"


@dataclass(frozen=True, repr=False)
class RegistrationRateLimitBucket:
    scope: RegistrationRateLimitScope
    _hmac_input: str = field(repr=False)
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RegistrationRateLimitScope):
            raise TypeError("Registration rate scope is invalid")
        if not isinstance(self._hmac_input, str) or not self._hmac_input:
            raise ValueError("Registration rate identity is invalid")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool):
            raise TypeError("Registration rate limit is invalid")
        if not isinstance(self.window_seconds, int) or isinstance(
            self.window_seconds, bool
        ):
            raise TypeError("Registration rate window is invalid")
        if self.limit <= 0 or self.window_seconds <= 0:
            raise ValueError("Registration rate policy is invalid")

    def as_limiter_arguments(self) -> tuple[str, str, int, int]:
        return (
            self.scope.value,
            self._hmac_input,
            self.limit,
            self.window_seconds,
        )

    def __repr__(self) -> str:
        return (
            "RegistrationRateLimitBucket("
            f"scope={self.scope.value!r}, hmac_input=<redacted>, "
            f"limit={self.limit!r}, window_seconds={self.window_seconds!r})"
        )


def build_registration_rate_limit_buckets(
    *,
    current_user: User,
    client_ip: ResolvedClientIp,
    config: RegistrationOtpConfig,
) -> tuple[RegistrationRateLimitBucket, ...]:
    if not isinstance(current_user, User) or current_user.is_active is not True:
        raise ValueError("Registration rate identity is invalid")
    if not isinstance(current_user.id, UUID):
        raise ValueError("Registration rate identity is invalid")
    if not isinstance(client_ip, ResolvedClientIp):
        raise ValueError("Registration rate identity is invalid")
    if not isinstance(config, RegistrationOtpConfig):
        raise TypeError("Registration rate policy is invalid")
    try:
        canonical_phone = normalize_uzbekistan_phone(current_user.phone)
    except (PhoneNormalizationError, TypeError, AttributeError):
        raise ValueError("Registration rate identity is invalid") from None

    window = config.rate_limit_window_seconds
    return (
        RegistrationRateLimitBucket(
            scope=RegistrationRateLimitScope.PHONE,
            _hmac_input=f"otp-registration-issue:phone:{canonical_phone}",
            limit=config.rate_limit_phone_attempts,
            window_seconds=window,
        ),
        RegistrationRateLimitBucket(
            scope=RegistrationRateLimitScope.USER,
            _hmac_input=f"otp-registration-issue:user:{current_user.id}",
            limit=config.rate_limit_user_attempts,
            window_seconds=window,
        ),
        RegistrationRateLimitBucket(
            scope=RegistrationRateLimitScope.IP,
            _hmac_input=(f"otp-registration-issue:ip:{client_ip.as_hmac_input()}"),
            limit=config.rate_limit_ip_attempts,
            window_seconds=window,
        ),
    )
