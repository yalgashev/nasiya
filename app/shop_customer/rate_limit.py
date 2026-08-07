"""Closed TX-B enumeration defense for exact-phone customer linking."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from app.auth.error_codes import ErrorCode
from app.auth.rate_limit import AuthRateLimiter, RateLimitResult
from app.settings import Settings
from app.shop_customer.contracts import DetachedShopCustomerAuthority
from app.telegram.client_ip import ResolvedClientIp

SHOP_CUSTOMER_LINK_ACTOR_SCOPE: Final = "shop_customer_link_actor"
SHOP_CUSTOMER_LINK_SHOP_SCOPE: Final = "shop_customer_link_shop"
SHOP_CUSTOMER_LINK_PHONE_SCOPE: Final = "shop_customer_link_phone"
SHOP_CUSTOMER_LINK_IP_SCOPE: Final = "shop_customer_link_ip"


@dataclass(frozen=True, slots=True, repr=False)
class ShopCustomerLinkRateLimitResult:
    allowed: bool
    error_code: ErrorCode | None = None

    def __repr__(self) -> str:
        return (
            "ShopCustomerLinkRateLimitResult("
            f"allowed={self.allowed!r}, error_code={self.error_code!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _RateBucket:
    scope: str
    raw_key: str
    allowed_attempts: int

    def __repr__(self) -> str:
        return (
            "_RateBucket("
            f"scope={self.scope!r}, raw_key=<redacted>, "
            "allowed_attempts=<configured>)"
        )


def record_shop_customer_link_attempt(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    authority: DetachedShopCustomerAuthority,
    submitted_phone: str,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> ShopCustomerLinkRateLimitResult:
    """Check-and-record every bucket in a short transaction owned by TX-B."""

    _validate_inputs(authority, submitted_phone, client_ip)
    with session_factory.begin() as db:
        limiter = AuthRateLimiter(db=db, settings=settings)
        results = tuple(
            limiter.record_failure(
                bucket.scope,
                bucket.raw_key,
                now,
                _existing_limiter_threshold(bucket.allowed_attempts),
                settings.shop_customer_link_rate_limit_window_seconds,
            )
            for bucket in _buckets(
                settings=settings,
                authority=authority,
                submitted_phone=submitted_phone,
                client_ip=client_ip,
            )
        )
    return _result(results)


def _buckets(
    *,
    settings: Settings,
    authority: DetachedShopCustomerAuthority,
    submitted_phone: str,
    client_ip: ResolvedClientIp,
) -> tuple[_RateBucket, ...]:
    return (
        _RateBucket(
            scope=SHOP_CUSTOMER_LINK_ACTOR_SCOPE,
            raw_key=f"shop_customer_link:actor:{authority.actor_user_id}",
            allowed_attempts=settings.shop_customer_link_rate_limit_actor_attempts,
        ),
        _RateBucket(
            scope=SHOP_CUSTOMER_LINK_SHOP_SCOPE,
            raw_key=f"shop_customer_link:shop:{authority.current_shop_id}",
            allowed_attempts=settings.shop_customer_link_rate_limit_shop_attempts,
        ),
        _RateBucket(
            scope=SHOP_CUSTOMER_LINK_PHONE_SCOPE,
            raw_key=f"shop_customer_link:phone:{submitted_phone}",
            allowed_attempts=settings.shop_customer_link_rate_limit_phone_attempts,
        ),
        _RateBucket(
            scope=SHOP_CUSTOMER_LINK_IP_SCOPE,
            raw_key=f"shop_customer_link:ip:{client_ip.as_hmac_input()}",
            allowed_attempts=settings.shop_customer_link_rate_limit_ip_attempts,
        ),
    )


def _result(results: tuple[RateLimitResult, ...]) -> ShopCustomerLinkRateLimitResult:
    if any(not result.allowed for result in results):
        return ShopCustomerLinkRateLimitResult(
            allowed=False,
            error_code=ErrorCode.RATE_LIMITED,
        )
    return ShopCustomerLinkRateLimitResult(allowed=True)


def _existing_limiter_threshold(allowed_attempts: int) -> int:
    return allowed_attempts + 1


def _validate_inputs(
    authority: object,
    submitted_phone: object,
    client_ip: object,
) -> None:
    if not isinstance(authority, DetachedShopCustomerAuthority):
        raise TypeError("authority must come from the closed TX-A adapter")
    if not isinstance(submitted_phone, str):
        raise TypeError("submitted_phone must be transient text")
    if not isinstance(client_ip, ResolvedClientIp):
        raise TypeError("client_ip must come from the trusted IP resolver")
