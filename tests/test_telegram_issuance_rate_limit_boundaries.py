import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.rate_limit import hash_rate_limit_key
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.rate_limit import (
    TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
    TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
    TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
    TelegramLinkIssuanceRateLimitPolicy,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-boundaries"


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False

    def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True

    def __getattr__(self, name: str):
        return getattr(self.session, name)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(
    engine: Engine,
    *,
    user_attempts: int = 3,
    phone_attempts: int = 3,
    ip_attempts: int = 20,
    window_seconds: int = 900,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        telegram_link_rate_limit_window_seconds=window_seconds,
        telegram_link_rate_limit_user_attempts=user_attempts,
        telegram_link_rate_limit_phone_attempts=phone_attempts,
        telegram_link_rate_limit_ip_attempts=ip_attempts,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def make_policy(
    db_session: Session,
    engine: Engine,
    *,
    user_attempts: int = 3,
    phone_attempts: int = 3,
    ip_attempts: int = 20,
    window_seconds: int = 900,
) -> TelegramLinkIssuanceRateLimitPolicy:
    return TelegramLinkIssuanceRateLimitPolicy(
        db=db_session,
        settings=make_settings(
            engine,
            user_attempts=user_attempts,
            phone_attempts=phone_attempts,
            ip_attempts=ip_attempts,
            window_seconds=window_seconds,
        ),
    )


def record_attempts(
    policy: TelegramLinkIssuanceRateLimitPolicy,
    user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
    count: int,
) -> list[bool]:
    return [
        policy.record_attempt(
            user,
            client_ip,
            now + timedelta(seconds=offset),
        ).allowed
        for offset in range(count)
    ]


def get_rate_limit_record(
    db_session: Session,
    settings: Settings,
    scope: str,
    raw_key: str,
) -> AuthRateLimit:
    record = db_session.scalar(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == scope,
            AuthRateLimit.key_hash == hash_rate_limit_key(settings, raw_key),
        )
    )
    assert record is not None
    return record


def stored_rate_limit_text(db_session: Session) -> str:
    rows = db_session.execute(
        text(
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text "
            "FROM auth_rate_limits"
        )
    ).all()
    return "|".join(str(value) for row in rows for value in row)


@pytest.mark.integration
def test_user_bucket_allows_three_and_rejects_fourth(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=3,
        phone_attempts=10,
        ip_attempts=10,
    )
    user = add_user(db_session, "+998900005001")

    allowed = record_attempts(
        policy,
        user,
        ResolvedClientIp("203.0.113.10"),
        datetime(2026, 7, 24, 14, 0, tzinfo=UTC),
        4,
    )

    assert allowed == [True, True, True, False]


@pytest.mark.integration
def test_phone_bucket_allows_three_and_rejects_fourth(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=10,
        phone_attempts=3,
        ip_attempts=10,
    )
    user = add_user(db_session, "+998900005002")

    allowed = record_attempts(
        policy,
        user,
        ResolvedClientIp("203.0.113.11"),
        datetime(2026, 7, 24, 14, 5, tzinfo=UTC),
        4,
    )

    assert allowed == [True, True, True, False]


@pytest.mark.integration
def test_ip_bucket_allows_twenty_and_rejects_twenty_first(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=1,
        phone_attempts=1,
        ip_attempts=20,
    )
    client_ip = ResolvedClientIp("203.0.113.12")
    now = datetime(2026, 7, 24, 14, 10, tzinfo=UTC)

    allowed = [
        policy.record_attempt(
            add_user(db_session, f"+998900005{index:03d}"),
            client_ip,
            now + timedelta(seconds=index),
        ).allowed
        for index in range(10, 31)
    ]

    assert allowed == [True] * 20 + [False]


@pytest.mark.integration
def test_window_boundary_resets_only_at_reset_time(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=3,
        phone_attempts=10,
        ip_attempts=10,
        window_seconds=900,
    )
    settings = policy.settings
    user = add_user(db_session, "+998900005031")
    client_ip = ResolvedClientIp("203.0.113.13")
    now = datetime(2026, 7, 24, 14, 15, tzinfo=UTC)
    reset_at = now + timedelta(seconds=900)

    first_three = record_attempts(policy, user, client_ip, now, 3)
    before_reset = policy.record_attempt(
        user,
        client_ip,
        reset_at - timedelta(seconds=1),
    )
    at_reset = policy.record_attempt(user, client_ip, reset_at)
    user_record = get_rate_limit_record(
        db_session,
        settings,
        TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
        f"{TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX}{user.id}",
    )

    assert first_three == [True, True, True]
    assert before_reset.allowed is False
    assert before_reset.error_code is ErrorCode.RATE_LIMITED
    assert at_reset.allowed is True
    assert user_record.window_started_at == reset_at
    assert user_record.attempt_count == 1


@pytest.mark.integration
def test_user_buckets_are_independent_but_ip_bucket_is_shared(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=1,
        phone_attempts=1,
        ip_attempts=2,
    )
    settings = policy.settings
    client_ip = ResolvedClientIp("203.0.113.14")
    now = datetime(2026, 7, 24, 14, 20, tzinfo=UTC)
    first_user = add_user(db_session, "+998900005032")
    second_user = add_user(db_session, "+998900005033")
    third_user = add_user(db_session, "+998900005034")

    first = policy.record_attempt(first_user, client_ip, now)
    second = policy.record_attempt(second_user, client_ip, now + timedelta(seconds=1))
    third = policy.record_attempt(third_user, client_ip, now + timedelta(seconds=2))
    ip_record = get_rate_limit_record(
        db_session,
        settings,
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
        f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}{client_ip.as_hmac_input()}",
    )
    user_records = db_session.scalars(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE
        )
    ).all()

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.error_code is ErrorCode.RATE_LIMITED
    assert len(user_records) == 3
    assert ip_record.attempt_count == 3


@pytest.mark.integration
def test_canonical_phone_scope_is_consistent(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=10,
        phone_attempts=3,
        ip_attempts=10,
    )
    settings = policy.settings
    user = add_user(db_session, "+998900005035")
    now = datetime(2026, 7, 24, 14, 25, tzinfo=UTC)

    first = policy.record_attempt(user, ResolvedClientIp("203.0.113.15"), now)
    second = policy.record_attempt(
        user,
        ResolvedClientIp("203.0.113.16"),
        now + timedelta(seconds=1),
    )
    phone_records = db_session.scalars(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE
        )
    ).all()
    phone_record = get_rate_limit_record(
        db_session,
        settings,
        TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
        f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{user.phone}",
    )

    assert first.allowed is True
    assert second.allowed is True
    assert len(phone_records) == 1
    assert phone_record.attempt_count == 2


@pytest.mark.integration
def test_equivalent_canonical_ip_representations_share_one_bucket(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=1,
        phone_attempts=1,
        ip_attempts=20,
    )
    settings = policy.settings
    now = datetime(2026, 7, 24, 14, 30, tzinfo=UTC)

    first = policy.record_attempt(
        add_user(db_session, "+998900005036"),
        ResolvedClientIp("203.0.113.17"),
        now,
    )
    second = policy.record_attempt(
        add_user(db_session, "+998900005037"),
        ResolvedClientIp("::ffff:203.0.113.17"),
        now + timedelta(seconds=1),
    )
    ip_records = db_session.scalars(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE
        )
    ).all()
    ip_record = get_rate_limit_record(
        db_session,
        settings,
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
        f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}203.0.113.17",
    )

    assert first.allowed is True
    assert second.allowed is True
    assert len(ip_records) == 1
    assert ip_record.attempt_count == 2


@pytest.mark.integration
def test_auth_rate_limit_table_stores_no_raw_user_phone_or_ip_values(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(db_session, m2_test_database)
    settings = policy.settings
    user = add_user(db_session, "+998900005038")
    client_ip = ResolvedClientIp("::ffff:203.0.113.18")

    result = policy.record_attempt(
        user,
        client_ip,
        datetime(2026, 7, 24, 14, 35, tzinfo=UTC),
    )
    records = db_session.scalars(select(AuthRateLimit)).all()
    key_hashes = {record.key_hash for record in records}
    stored_text = stored_rate_limit_text(db_session)

    assert result.allowed is True
    assert len(records) == 3
    assert str(user.id) not in stored_text
    assert user.phone not in stored_text
    assert client_ip.as_hmac_input() not in stored_text
    assert hash_rate_limit_key(settings, str(user.id)) not in key_hashes
    assert hash_rate_limit_key(settings, user.phone) not in key_hashes
    assert hash_rate_limit_key(settings, client_ip.as_hmac_input()) not in key_hashes


@pytest.mark.integration
def test_rejected_bucket_identity_is_not_in_error_or_logs(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    policy = make_policy(
        db_session,
        m2_test_database,
        user_attempts=1,
        phone_attempts=10,
        ip_attempts=10,
    )
    user = add_user(db_session, "+998900005039")
    client_ip = ResolvedClientIp("203.0.113.19")
    now = datetime(2026, 7, 24, 14, 40, tzinfo=UTC)
    logger = logging.getLogger("tests.telegram_issuance_rate_limit_boundaries")

    policy.record_attempt(user, client_ip, now)
    blocked = policy.record_attempt(user, client_ip, now + timedelta(seconds=1))
    with caplog.at_level(logging.INFO):
        logger.info("blocked result %s %r %s", blocked, blocked, f"{blocked}")

    result_text = f"{blocked!r} {blocked.public_error} {caplog.text}"
    forbidden_terms = {
        "user",
        "phone",
        "ip",
        TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
        str(user.id),
        user.phone,
        client_ip.as_hmac_input(),
    }

    assert blocked.allowed is False
    assert blocked.error_code is ErrorCode.RATE_LIMITED
    for forbidden_term in forbidden_terms:
        assert forbidden_term not in result_text


@pytest.mark.integration
def test_policy_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    session_spy = SessionSpy(first_session)
    try:
        user = add_user(first_session, "+998900005040")
        policy = TelegramLinkIssuanceRateLimitPolicy(
            db=session_spy,
            settings=settings,
        )

        result = policy.record_attempt(
            user,
            ResolvedClientIp("203.0.113.20"),
            datetime(2026, 7, 24, 14, 45, tzinfo=UTC),
        )
        stored_count = second_session.scalar(
            select(func.count()).select_from(AuthRateLimit)
        )

        assert result.allowed is True
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert stored_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()
