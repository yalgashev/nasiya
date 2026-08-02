from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from inspect import signature
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.rate_limit import AuthRateLimiter, hash_rate_limit_key
from app.customer_activation.contracts import (
    CustomerActivationActor,
    CustomerActivationBrowserContext,
    RegistrationOtpCooldown,
    RegistrationOtpPendingDelivery,
)
from app.customer_activation.rate_limit import (
    RegistrationIssuanceRateLimitPolicy,
    RegistrationRateLimitBucket,
    RegistrationRateLimitResult,
    RegistrationRateLimitScope,
    build_registration_rate_limit_buckets,
)
from app.customer_activation.service import (
    AuthenticatedActivationContext,
    request_new_registration_otp,
    request_registration_otp,
)
from app.otp.contracts import OtpChallengeStatus, OtpPurpose
from app.otp.models import OtpChallenge
from app.otp.repository import create_pending_challenge
from app.otp.web_presentation import OtpWebLanguage
from app.settings import RegistrationOtpConfig, Settings
from app.telegram.client_ip import ResolvedClientIp
from tests.m11_seed import (
    NOW,
    REGISTRATION_DIGEST,
    seed_registration_snapshot,
    synthetic_identity_crypto_config,
)

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


@pytest.mark.integration
def test_registration_rate_check_and_record_exact_scopes_and_boundary(
    m2_test_database: Engine,
) -> None:
    settings = _settings()
    now = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)
    with Session(m2_test_database) as session, session.begin():
        user = _user()
        session.add(user)

    outcomes: list[RegistrationRateLimitResult] = []
    for offset in range(4):
        with Session(m2_test_database) as session, session.begin():
            current_user = session.get(User, _USER_ID)
            assert current_user is not None
            outcomes.append(
                RegistrationIssuanceRateLimitPolicy(
                    session=session,
                    settings=settings,
                ).check_and_record(
                    current_user=current_user,
                    client_ip=ResolvedClientIp(_IP),
                    now=now + timedelta(seconds=offset),
                )
            )

    with Session(m2_test_database) as session, session.begin():
        before = tuple(
            (row.scope, row.attempt_count)
            for row in session.scalars(
                select(AuthRateLimit).order_by(AuthRateLimit.scope)
            )
        )
        current_user = session.get(User, _USER_ID)
        assert current_user is not None
        preblocked = RegistrationIssuanceRateLimitPolicy(
            session=session,
            settings=settings,
        ).check_and_record(
            current_user=current_user,
            client_ip=ResolvedClientIp(_IP),
            now=now + timedelta(seconds=5),
        )
        after = tuple(
            (row.scope, row.attempt_count)
            for row in session.scalars(
                select(AuthRateLimit).order_by(AuthRateLimit.scope)
            )
        )
        still_blocked = RegistrationIssuanceRateLimitPolicy(
            session=session,
            settings=settings,
        ).check_and_record(
            current_user=current_user,
            client_ip=ResolvedClientIp(_IP),
            now=now + timedelta(seconds=899),
        )
        assert session.scalar(select(func.count()).select_from(User)) == 1

    with Session(m2_test_database) as session, session.begin():
        current_user = session.get(User, _USER_ID)
        assert current_user is not None
        reset = RegistrationIssuanceRateLimitPolicy(
            session=session,
            settings=settings,
        ).check_and_record(
            current_user=current_user,
            client_ip=ResolvedClientIp(_IP),
            now=now + timedelta(seconds=900),
        )
        reset_rows = tuple(
            (row.scope, row.attempt_count, row.window_started_at)
            for row in session.scalars(
                select(AuthRateLimit).order_by(AuthRateLimit.scope)
            )
        )

    assert [outcome.allowed for outcome in outcomes] == [True, True, True, False]
    assert outcomes[-1].error_code is ErrorCode.RATE_LIMITED
    assert not preblocked.allowed
    assert not still_blocked.allowed
    assert reset.allowed
    assert before == after
    assert {scope for scope, _ in after} == {
        scope.value for scope in RegistrationRateLimitScope
    }
    assert {attempt_count for _, attempt_count in after} == {4}
    assert {attempt_count for _, attempt_count, _ in reset_rows} == {1}
    assert {started_at for _, _, started_at in reset_rows} == {
        now + timedelta(seconds=900)
    }


@pytest.mark.integration
def test_registration_ip_scope_allows_twenty_and_blocks_twenty_first(
    m2_test_database: Engine,
) -> None:
    settings = _settings()
    now = datetime(2026, 8, 2, 10, 15, tzinfo=UTC)
    users = tuple(
        User(
            id=UUID(int=10_000 + offset),
            phone=f"+99890200{offset:04d}",
            password_hash="synthetic-password-hash",
            is_active=True,
        )
        for offset in range(21)
    )
    with Session(m2_test_database) as session, session.begin():
        session.add_all(users)
        user_ids = tuple(user.id for user in users)

    outcomes: list[bool] = []
    for offset, user_id in enumerate(user_ids):
        with Session(m2_test_database) as session, session.begin():
            current_user = session.get(User, user_id)
            assert current_user is not None
            outcomes.append(
                RegistrationIssuanceRateLimitPolicy(
                    session=session,
                    settings=settings,
                )
                .check_and_record(
                    current_user=current_user,
                    client_ip=ResolvedClientIp(_IP),
                    now=now + timedelta(seconds=offset),
                )
                .allowed
            )

    with Session(m2_test_database) as session:
        ip_row = session.scalar(
            select(AuthRateLimit).where(
                AuthRateLimit.scope == RegistrationRateLimitScope.IP.value
            )
        )

    assert outcomes == ([True] * 20) + [False]
    assert ip_row is not None
    assert ip_row.attempt_count == 21


@pytest.mark.integration
def test_registration_rate_concurrent_cap_is_deterministic(
    m2_test_database: Engine,
) -> None:
    settings = _settings()
    now = datetime(2026, 8, 2, 10, 30, tzinfo=UTC)
    with Session(m2_test_database) as session, session.begin():
        session.add(_user())
    start = Barrier(5)

    def attempt() -> bool:
        with Session(m2_test_database) as session, session.begin():
            current_user = session.get(User, _USER_ID)
            assert current_user is not None
            start.wait(timeout=5)
            return (
                RegistrationIssuanceRateLimitPolicy(
                    session=session,
                    settings=settings,
                )
                .check_and_record(
                    current_user=current_user,
                    client_ip=ResolvedClientIp(_IP),
                    now=now,
                )
                .allowed
            )

    executor = ThreadPoolExecutor(max_workers=5)
    try:
        futures = [executor.submit(attempt) for _ in range(5)]
        completed, pending = wait(futures, timeout=10)
        assert not pending
        outcomes = [future.result() for future in completed]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert outcomes.count(True) == 3
    assert outcomes.count(False) == 2
    with Session(m2_test_database) as session:
        rows = tuple(session.scalars(select(AuthRateLimit)))
        assert len(rows) == 3
        assert {row.attempt_count for row in rows} == {5}


@pytest.mark.integration
def test_registration_rate_persists_only_scoped_hmac_keys(
    m2_test_database: Engine,
) -> None:
    settings = _settings()
    now = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
    with Session(m2_test_database) as session, session.begin():
        user = _user()
        session.add(user)
        session.flush()
        result = RegistrationIssuanceRateLimitPolicy(
            session=session,
            settings=settings,
        ).check_and_record(
            current_user=user,
            client_ip=ResolvedClientIp(_IP),
            now=now,
        )

    with Session(m2_test_database) as session:
        rows = tuple(session.scalars(select(AuthRateLimit)))
        stored = " ".join(f"{row.scope} {row.key_hash}" for row in rows)
        count = session.scalar(select(func.count()).select_from(AuthRateLimit))

    assert result.allowed
    assert count == 3
    assert _PHONE not in stored
    assert str(_USER_ID) not in stored
    assert _IP not in stored
    expected_buckets = build_registration_rate_limit_buckets(
        current_user=_user(),
        client_ip=ResolvedClientIp(_IP),
        config=settings.require_registration_otp_config(),
    )
    assert {(row.scope, row.key_hash) for row in rows} == {
        (
            bucket.scope.value,
            hash_rate_limit_key(settings, bucket.as_limiter_arguments()[1]),
        )
        for bucket in expected_buckets
    }


@pytest.mark.integration
def test_registration_scopes_and_cooldown_are_isolated_from_login(
    m2_test_database: Engine,
) -> None:
    settings = _settings()
    factory = sessionmaker(bind=m2_test_database, expire_on_commit=False)
    with Session(m2_test_database) as session, session.begin():
        snapshot = seed_registration_snapshot(
            session,
            phone=_PHONE,
        )
        login_scope_keys = (
            ("otp-login-issue:phone", f"otp-login-issue:phone:{_PHONE}"),
            (
                "otp-login-issue:user",
                f"otp-login-issue:user:{snapshot.user_id}",
            ),
            ("otp-login-issue:ip", f"otp-login-issue:ip:{_IP}"),
        )
        login = create_pending_challenge(
            session,
            browser_binding_digest=REGISTRATION_DIGEST,
            now=NOW,
            purpose=OtpPurpose.LOGIN,
            user_id=snapshot.user_id,
            telegram_link_id=snapshot.telegram_link_id,
            telegram_linked_at=snapshot.telegram_linked_at,
        )
        limiter = AuthRateLimiter(db=session, settings=settings)
        for scope, raw_key in login_scope_keys:
            assert limiter.record_failure(scope, raw_key, NOW, 6, 900).allowed
        login_id = login.id

    context = AuthenticatedActivationContext(
        actor=CustomerActivationActor(snapshot.user_id),
        browser=CustomerActivationBrowserContext(
            current_session_id=_USER_ID,
            browser_binding_digest=REGISTRATION_DIGEST,
        ),
        trusted_client_ip=ResolvedClientIp(_IP),
        _canonical_account_phone=_PHONE,
    )
    initial = request_registration_otp(
        factory,
        context=context,
        settings=settings,
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.UZ_LATN,
        now=NOW + timedelta(seconds=1),
    )
    before_boundary = request_new_registration_otp(
        factory,
        context=context,
        settings=settings,
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.UZ_LATN,
        now=NOW + timedelta(seconds=60),
    )
    at_boundary = request_new_registration_otp(
        factory,
        context=context,
        settings=settings,
        identity_crypto_config=synthetic_identity_crypto_config(),
        language=OtpWebLanguage.UZ_LATN,
        now=NOW + timedelta(seconds=61),
    )

    with Session(m2_test_database) as session:
        login_row = session.get(OtpChallenge, login_id)
        login_rate_rows = tuple(
            (row.scope, row.attempt_count, row.window_started_at)
            for row in session.scalars(
                select(AuthRateLimit)
                .where(AuthRateLimit.scope.like("otp-login-%"))
                .order_by(AuthRateLimit.scope)
            )
        )
        registration_rows = tuple(
            session.scalars(
                select(OtpChallenge)
                .where(OtpChallenge.purpose == OtpPurpose.REGISTRATION.value)
                .order_by(OtpChallenge.created_at)
            )
        )

    assert isinstance(initial, RegistrationOtpPendingDelivery)
    assert isinstance(before_boundary, RegistrationOtpCooldown)
    assert isinstance(at_boundary, RegistrationOtpPendingDelivery)
    assert login_row is not None
    assert login_row.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert login_rate_rows == tuple(
        (scope, 1, NOW) for scope, _ in sorted(login_scope_keys)
    )
    assert [row.status for row in registration_rows] == [
        OtpChallengeStatus.SUPERSEDED.value,
        OtpChallengeStatus.PENDING_DISPATCH.value,
    ]
