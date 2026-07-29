from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.otp.issuance as issuance_module
from app.auth.models import AuthRateLimit, User
from app.db import create_database_session_factory
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
)
from app.otp.issuance import (
    lookup_login_otp_eligibility,
    request_login_otp,
    request_new_login_code,
)
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink
from app.telegram.service import consume_start_token, issue_relink_token, unlink

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
OTP_HMAC_KEY = "test-otp-hmac-key-for-issuance-at-least-32-chars"
RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-issuance"
DEFAULT_TELEGRAM_CHAT_ID = object()


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine, **overrides) -> Settings:
    values = {
        "app_environment": "testing",
        "debug": False,
        "database_url": engine.url.render_as_string(hide_password=False),
        "session_cookie_secure": False,
        "rate_limit_hmac_key": RATE_LIMIT_HMAC_KEY,
        "otp_hmac_key": OTP_HMAC_KEY,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def add_user(
    session: Session,
    phone: str = "+998900008001",
    *,
    is_active: bool = True,
) -> User:
    user = User(phone=phone, is_active=is_active)
    session.add(user)
    session.flush()
    return user


def add_link(
    session: Session,
    user: User,
    *,
    linked_at: datetime = NOW,
    telegram_chat_id: int | None | object = DEFAULT_TELEGRAM_CHAT_ID,
    unlinked_at: datetime | None = None,
) -> TelegramLink:
    if telegram_chat_id is DEFAULT_TELEGRAM_CHAT_ID:
        link_count = session.scalar(select(func.count()).select_from(TelegramLink)) or 0
        chat_id: int | None = 9_980_100_001 + link_count
    else:
        chat_id = telegram_chat_id if isinstance(telegram_chat_id, int) else None
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=chat_id,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        updated_at=unlinked_at or linked_at,
    )
    session.add(link)
    session.flush()
    return link


def issue(
    session: Session,
    settings: Settings,
    *,
    phone_input: str = "+998 90 000-80-01",
    digest: str = "a" * 64,
    client_ip: ResolvedClientIp | None = None,
    now: datetime = NOW,
    dummy_calls: list[str] | None = None,
):
    def dummy_work(_key) -> None:
        if dummy_calls is not None:
            dummy_calls.append("dummy")

    return request_login_otp(
        session,
        settings,
        phone_input=phone_input,
        browser_binding_digest=digest,
        client_ip=client_ip or ResolvedClientIp("203.0.113.10"),
        locale="uz-Latn",
        now=now,
        dummy_work=dummy_work,
    )


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def seed_committed_user_and_link(
    engine: Engine,
    *,
    phone: str = "+998900008090",
    chat_id: int = 9_980_100_090,
) -> None:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, phone)
        add_link(session, user, telegram_chat_id=chat_id)
        session.commit()
    finally:
        session.close()


@pytest.mark.integration
def test_pre_auth_eligibility_is_neutral_and_uses_canonical_phone(
    db_session: Session,
) -> None:
    inactive = add_user(db_session, "+998900008010", is_active=False)
    unlinked_user = add_user(db_session, "+998900008011")
    add_link(
        db_session,
        unlinked_user,
        telegram_chat_id=None,
        unlinked_at=NOW,
    )
    linked_user = add_user(db_session, "+998900008012")
    linked = add_link(db_session, linked_user)

    assert not lookup_login_otp_eligibility(
        db_session,
        phone_input="+998900008099",
    ).eligible
    assert not lookup_login_otp_eligibility(
        db_session,
        phone_input=inactive.phone,
    ).eligible
    assert not lookup_login_otp_eligibility(
        db_session,
        phone_input=unlinked_user.phone,
    ).eligible

    result = lookup_login_otp_eligibility(
        db_session,
        phone_input="90 000-80-12",
    )
    assert result.eligible is True
    assert result.target is not None
    assert result.target.user.id == linked_user.id
    assert result.target.telegram_link.id == linked.id
    assert result.target.canonical_phone == "+998900008012"


@pytest.mark.integration
def test_unknown_path_records_phone_ip_limits_runs_dummy_work_and_mutates_no_otp_rows(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    dummy_calls: list[str] = []

    result = issue(
        db_session,
        settings,
        phone_input="+998900008020",
        dummy_calls=dummy_calls,
    )

    assert result.outcome is OtpInternalOutcome.OTP_NOT_ELIGIBLE
    assert dummy_calls == ["dummy"]
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0
    rate_limit_rows = list(db_session.scalars(select(AuthRateLimit)).all())
    assert {row.scope for row in rate_limit_rows} == {
        "otp-login-issue:phone",
        "otp-login-issue:ip",
    }
    rendered_rows = " ".join(f"{row.scope} {row.key_hash}" for row in rate_limit_rows)
    assert "+998900008020" not in rendered_rows
    assert "203.0.113.10" not in rendered_rows


@pytest.mark.integration
def test_successful_issue_creates_pending_challenge_dispatch_and_event_atomically(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    link = add_link(db_session, user)
    dummy_calls: list[str] = []

    result = issue(db_session, settings, dummy_calls=dummy_calls)

    assert result.accepted is True
    assert dummy_calls == ["dummy"]
    assert result.challenge is not None
    assert result.dispatch is not None
    assert result.challenge.user_id == user.id
    assert result.challenge.telegram_link_id == link.id
    assert result.challenge.telegram_linked_at == link.linked_at
    assert result.challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert result.challenge.code_mac is None
    assert result.dispatch.challenge_id == result.challenge.id
    assert result.dispatch.status == OtpDispatchStatus.PENDING.value
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1
    assert count_table(db_session, OtpChallengeEvent) == 1
    event = db_session.scalar(select(OtpChallengeEvent))
    assert event is not None
    assert event.action == OtpChallengeEventAction.ISSUED.value


@pytest.mark.integration
def test_second_allowed_issue_supersedes_old_challenge_and_cancels_dispatch(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    add_link(db_session, user)
    first = issue(db_session, settings, digest="b" * 64)
    assert first.challenge is not None
    assert first.dispatch is not None

    second = issue(
        db_session,
        settings,
        digest="c" * 64,
        now=NOW + timedelta(seconds=1),
    )

    assert second.accepted is True
    assert second.challenge is not None
    assert first.challenge.status == OtpChallengeStatus.SUPERSEDED.value
    assert first.dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert second.challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    outstanding = list(
        db_session.scalars(
            select(OtpChallenge).where(
                OtpChallenge.status.in_(
                    [
                        OtpChallengeStatus.PENDING_DISPATCH.value,
                        OtpChallengeStatus.ACTIVE.value,
                    ]
                )
            )
        ).all()
    )
    assert outstanding == [second.challenge]
    actions = list(
        db_session.scalars(
            select(OtpChallengeEvent.action).order_by(OtpChallengeEvent.occurred_at)
        ).all()
    )
    assert actions == [
        OtpChallengeEventAction.ISSUED.value,
        OtpChallengeEventAction.SUPERSEDED.value,
        OtpChallengeEventAction.ISSUED.value,
    ]


@pytest.mark.integration
def test_parallel_issue_requests_have_exactly_one_final_outstanding(
    m2_test_database: Engine,
) -> None:
    seed_committed_user_and_link(m2_test_database)
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    barrier = Barrier(2)

    def request_candidate(digest: str) -> str:
        session = session_factory()
        try:
            barrier.wait()
            result = issue(
                session,
                settings,
                phone_input="+998900008090",
                digest=digest,
                client_ip=ResolvedClientIp("203.0.113.12"),
            )
            session.commit()
            return result.outcome.value
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(request_candidate, ("4" * 64, "5" * 64)))

    session = session_factory()
    try:
        assert outcomes == [OtpInternalOutcome.OTP_PENDING.value] * 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(OtpChallenge)
                .where(
                    OtpChallenge.status.in_(
                        [
                            OtpChallengeStatus.PENDING_DISPATCH.value,
                            OtpChallengeStatus.ACTIVE.value,
                        ]
                    )
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(OtpDispatch)
                .where(
                    OtpDispatch.status.in_(
                        [
                            OtpDispatchStatus.PENDING.value,
                            OtpDispatchStatus.PREPARED.value,
                        ]
                    )
                )
            )
            == 1
        )
    finally:
        session.close()


@pytest.mark.integration
def test_parallel_new_code_requests_keep_one_outstanding_challenge(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    digest = "9" * 64
    with session_factory.begin() as session:
        user = add_user(session, "+998900008091")
        add_link(session, user, telegram_chat_id=9_980_100_091)
        initial = issue(
            session,
            settings,
            phone_input="+998900008091",
            digest=digest,
            now=NOW - timedelta(seconds=70),
        )
        assert initial.accepted is True

    barrier = Barrier(2)

    def request_candidate(index: int) -> str:
        with session_factory.begin() as session:
            barrier.wait()
            result = request_new_login_code(
                session,
                settings,
                browser_binding_digest=digest,
                client_ip=ResolvedClientIp(f"203.0.113.{30 + index}"),
                locale="uz-Latn",
                now=NOW,
            )
            return result.outcome.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(request_candidate, range(2)))

    with session_factory() as session:
        outstanding_count = (
            session.scalar(
                select(func.count())
                .select_from(OtpChallenge)
                .where(
                    OtpChallenge.status.in_(
                        [
                            OtpChallengeStatus.PENDING_DISPATCH.value,
                            OtpChallengeStatus.ACTIVE.value,
                        ]
                    )
                )
            )
            or 0
        )
        open_dispatch_count = (
            session.scalar(
                select(func.count())
                .select_from(OtpDispatch)
                .where(
                    OtpDispatch.status.in_(
                        [
                            OtpDispatchStatus.PENDING.value,
                            OtpDispatchStatus.PREPARED.value,
                        ]
                    )
                )
            )
            or 0
        )

    assert outcomes.count(OtpInternalOutcome.OTP_PENDING.value) >= 1
    assert set(outcomes).issubset(
        {
            OtpInternalOutcome.OTP_NOT_ELIGIBLE.value,
            OtpInternalOutcome.OTP_PENDING.value,
            OtpInternalOutcome.RATE_LIMITED.value,
        }
    )
    assert outstanding_count == 1
    assert open_dispatch_count == 1


@pytest.mark.integration
def test_same_browser_second_user_supersedes_old_without_cross_user_outstanding(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    first_user = add_user(db_session, "+998900008030")
    add_link(db_session, first_user)
    second_user = add_user(db_session, "+998900008031")
    add_link(db_session, second_user)
    first = issue(
        db_session,
        settings,
        phone_input="+998900008030",
        digest="6" * 64,
    )
    assert first.challenge is not None

    second = issue(
        db_session,
        settings,
        phone_input="+998900008031",
        digest="6" * 64,
        now=NOW + timedelta(seconds=1),
    )

    assert second.accepted is True
    assert second.challenge is not None
    assert first.challenge.status == OtpChallengeStatus.SUPERSEDED.value
    assert second.challenge.user_id == second_user.id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OtpChallenge)
            .where(
                OtpChallenge.user_id == first_user.id,
                OtpChallenge.status.in_(
                    [
                        OtpChallengeStatus.PENDING_DISPATCH.value,
                        OtpChallengeStatus.ACTIVE.value,
                    ]
                ),
            )
        )
        == 0
    )


@pytest.mark.integration
def test_rate_limit_rejection_preserves_old_challenge_and_dispatch(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database, otp_login_rate_limit_phone_attempts=2)
    user = add_user(db_session)
    add_link(db_session, user)
    first = issue(db_session, settings)
    assert first.challenge is not None
    assert first.dispatch is not None

    blocked = issue(
        db_session,
        settings,
        digest="c" * 64,
        now=NOW + timedelta(seconds=1),
    )

    assert blocked.outcome is OtpInternalOutcome.RATE_LIMITED
    assert first.challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert first.dispatch.status == OtpDispatchStatus.PENDING.value
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1


@pytest.mark.integration
def test_new_code_cooldown_boundary_creates_new_challenge_only_at_60_seconds(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    add_link(db_session, user)
    first = issue(db_session, settings, digest="d" * 64)
    assert first.challenge is not None
    assert first.dispatch is not None

    early = request_new_login_code(
        db_session,
        settings,
        browser_binding_digest="d" * 64,
        client_ip=ResolvedClientIp("203.0.113.11"),
        locale="uz-Latn",
        now=NOW + timedelta(seconds=59),
    )
    assert early.outcome is OtpInternalOutcome.RATE_LIMITED
    assert first.challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert first.dispatch.status == OtpDispatchStatus.PENDING.value

    allowed = request_new_login_code(
        db_session,
        settings,
        browser_binding_digest="d" * 64,
        client_ip=ResolvedClientIp("203.0.113.11"),
        locale="uz-Latn",
        now=NOW + timedelta(seconds=60),
    )

    assert allowed.accepted is True
    assert allowed.challenge is not None
    assert first.challenge.status == OtpChallengeStatus.SUPERSEDED.value
    assert first.dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert allowed.challenge.id != first.challenge.id


@pytest.mark.integration
def test_unlink_invalidates_outstanding_otp_challenge_in_same_transaction(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    add_link(db_session, user)
    issued = issue(db_session, settings)
    assert issued.challenge is not None
    assert issued.dispatch is not None

    unlink(db_session, user, NOW + timedelta(seconds=5))

    assert issued.challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert issued.dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OtpChallengeEvent)
            .where(
                OtpChallengeEvent.action
                == OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value
            )
        )
        == 1
    )


@pytest.mark.integration
def test_successful_relink_invalidates_outstanding_otp_challenge(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    add_link(db_session, user, telegram_chat_id=9_980_100_200)
    issued_otp = issue(db_session, settings)
    assert issued_otp.challenge is not None
    assert issued_otp.dispatch is not None
    relink_token = issue_relink_token(
        db_session,
        settings,
        user,
        ResolvedClientIp("203.0.113.20"),
        NOW + timedelta(seconds=1),
        token_generator=lambda _: "otp_relink_token",
    )

    consume_start_token(
        db_session,
        relink_token.raw_token,
        VerifiedPrivateTelegramChatIdentity(9_980_100_201),
        NOW + timedelta(seconds=2),
    )

    assert issued_otp.challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert issued_otp.dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OtpChallengeEvent)
            .where(
                OtpChallengeEvent.action
                == OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value
            )
        )
        == 1
    )


@pytest.mark.integration
def test_event_failure_rolls_back_issue_challenge_and_dispatch(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    add_link(db_session, user)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("forced event failure")

    monkeypatch.setattr(issuance_module, "append_challenge_event", fail_event)
    with pytest.raises(RuntimeError, match="forced event failure"):
        with db_session.begin_nested():
            issue(db_session, settings)

    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0
