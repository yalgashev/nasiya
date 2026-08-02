from __future__ import annotations

from dataclasses import FrozenInstanceError
from inspect import signature
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.auth.models import User
from app.customer_activation.rate_limit import (
    RegistrationRateLimitBucket,
    RegistrationRateLimitScope,
    build_registration_rate_limit_buckets,
)
from app.settings import RegistrationOtpConfig, Settings
from app.telegram.client_ip import ResolvedClientIp

_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"
_RATE_KEY = "test-registration-rate-key-at-least-32-characters"
_PHONE = "+998901234567"
_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_IP = "203.0.113.17"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": _DATABASE_URL,
        "session_cookie_secure": False,
        "rate_limit_hmac_key": _RATE_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _user(*, active: bool = True) -> User:
    return User(
        id=_USER_ID,
        phone=_PHONE,
        password_hash="synthetic-password-hash",
        is_active=active,
    )


def test_registration_settings_defaults_and_bounds_fail_closed() -> None:
    config = _settings().require_registration_otp_config()

    assert config == RegistrationOtpConfig(
        ttl_seconds=180,
        max_verify_attempts=5,
        resend_cooldown_seconds=60,
        rate_limit_window_seconds=900,
        rate_limit_phone_attempts=3,
        rate_limit_user_attempts=3,
        rate_limit_ip_attempts=20,
    )
    with pytest.raises(FrozenInstanceError):
        config.ttl_seconds = 181  # type: ignore[misc]

    invalid = (
        {"otp_registration_ttl_seconds": 59},
        {"otp_registration_ttl_seconds": 601},
        {"otp_registration_max_verify_attempts": 0},
        {"otp_registration_max_verify_attempts": 11},
        {"otp_registration_resend_cooldown_seconds": 0},
        {"otp_registration_resend_cooldown_seconds": 180},
        {"otp_registration_rate_limit_window_seconds": 0},
        {"otp_registration_rate_limit_phone_attempts": 0},
        {"otp_registration_rate_limit_user_attempts": 0},
        {"otp_registration_rate_limit_ip_attempts": 0},
    )
    for values in invalid:
        with pytest.raises(ValidationError):
            _settings(**values)


def test_malformed_non_empty_registration_environment_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "malformed-registration-setting-sentinel"
    monkeypatch.setenv("DATABASE_URL", _DATABASE_URL)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("RATE_LIMIT_HMAC_KEY", _RATE_KEY)
    monkeypatch.setenv("OTP_REGISTRATION_TTL_SECONDS", sentinel)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert sentinel not in str(exc_info.value)
    assert sentinel not in repr(exc_info.value)


def test_registration_rate_uses_server_phone_user_and_trusted_ip_once() -> None:
    assert "phone" not in signature(build_registration_rate_limit_buckets).parameters

    buckets = build_registration_rate_limit_buckets(
        current_user=_user(),
        client_ip=ResolvedClientIp(_IP),
        config=_settings().require_registration_otp_config(),
    )

    assert tuple(bucket.scope for bucket in buckets) == (
        RegistrationRateLimitScope.PHONE,
        RegistrationRateLimitScope.USER,
        RegistrationRateLimitScope.IP,
    )
    assert tuple(bucket.limit for bucket in buckets) == (3, 3, 20)
    assert tuple(bucket.window_seconds for bucket in buckets) == (900, 900, 900)
    limiter_arguments = tuple(bucket.as_limiter_arguments() for bucket in buckets)
    assert limiter_arguments == (
        (
            "otp-registration-issue:phone",
            f"otp-registration-issue:phone:{_PHONE}",
            3,
            900,
        ),
        (
            "otp-registration-issue:user",
            f"otp-registration-issue:user:{_USER_ID}",
            3,
            900,
        ),
        (
            "otp-registration-issue:ip",
            f"otp-registration-issue:ip:{_IP}",
            20,
            900,
        ),
    )


def test_registration_rate_bucket_repr_and_errors_redact_identities() -> None:
    buckets = build_registration_rate_limit_buckets(
        current_user=_user(),
        client_ip=ResolvedClientIp(_IP),
        config=RegistrationOtpConfig(),
    )
    rendered = " ".join(repr(bucket) for bucket in buckets)

    assert _PHONE not in rendered
    assert str(_USER_ID) not in rendered
    assert _IP not in rendered
    assert rendered.count("hmac_input=<redacted>") == 3

    with pytest.raises(ValueError) as exc_info:
        build_registration_rate_limit_buckets(
            current_user=_user(active=False),
            client_ip=ResolvedClientIp(_IP),
            config=RegistrationOtpConfig(),
        )
    assert _PHONE not in str(exc_info.value)
    assert str(_USER_ID) not in str(exc_info.value)
    assert _IP not in str(exc_info.value)


def test_registration_rate_bucket_rejects_untyped_scope_without_echo() -> None:
    with pytest.raises(TypeError, match="Registration rate scope is invalid"):
        RegistrationRateLimitBucket(
            scope="otp-registration-issue:phone",  # type: ignore[arg-type]
            _hmac_input="sensitive-sentinel",
            limit=3,
            window_seconds=900,
        )
