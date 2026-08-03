from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.otp.issuance as issuance_module
from app.auth.models import AuthRateLimit, User
from app.auth.rate_limit import hash_rate_limit_key
from app.customer.models import Customer
from app.customer_document.models import CustomerDocument
from app.db import create_database_session_factory
from app.offers.models import OfferAcceptance
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
)
from app.otp.issuance import (
    CoordinatedOtpIssueResult,
    coordinate_login_otp_request,
    coordinate_new_login_code_request,
    issue_login_otp_in_transaction,
    issue_new_login_code_in_transaction,
    lookup_login_otp_eligibility,
)
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import create_pending_challenge, create_pending_dispatch
from app.settings import Settings
from app.storage.models import ObjectFile
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink
from app.telegram.service import (
    bind_start_token_for_contact,
    consume_start_token,
    unlink,
)
from tests.telegram_issue_helpers import (
    issue_relink_token_in_one_test_transaction as issue_relink_token,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
OTP_HMAC_KEY = "test-otp-hmac-key-for-issuance-at-least-32-chars"
RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-issuance"
CONTACT_BINDING_KEY = SecretStr(RATE_LIMIT_HMAC_KEY)
DEFAULT_TELEGRAM_CHAT_ID = object()


class _SessionLifecycle:
    def __init__(self) -> None:
        self.sessions: list[_TrackingSession] = []
        self.phases: list[str] = []

    def register(self, session: _TrackingSession) -> None:
        self.sessions.append(session)

    def enter(self, phase: str, session: _TrackingSession) -> None:
        assert session in self.sessions
        current_index = self.sessions.index(session)
        assert all(
            candidate.tracking_closed for candidate in self.sessions[:current_index]
        )
        assert not session.tracking_closed
        self.phases.append(phase)

    def assert_all_closed(self) -> None:
        assert self.sessions
        assert all(session.tracking_closed for session in self.sessions)
        assert len({id(session) for session in self.sessions}) == len(self.sessions)


class _TrackingSession(Session):
    def __init__(
        self,
        *args: Any,
        lifecycle: _SessionLifecycle,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lifecycle = lifecycle
        self.tracking_closed = False
        lifecycle.register(self)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.tracking_closed = True


def _tracking_session_factory(
    engine: Engine,
    lifecycle: _SessionLifecycle,
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        class_=_TrackingSession,
        lifecycle=lifecycle,
    )


def _mark_tracking_phase(session: Session, phase: str) -> None:
    assert isinstance(session, _TrackingSession)
    session.lifecycle.enter(phase, session)


def _rate_limit_rows(session: Session) -> dict[tuple[str, str], int]:
    return {
        (row.scope, row.key_hash): row.attempt_count
        for row in session.scalars(select(AuthRateLimit)).all()
    }


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    assert {
        OfferAcceptance.__table__.metadata,
        Customer.__table__.metadata,
        CustomerDocument.__table__.metadata,
        ObjectFile.__table__.metadata,
    } == {OtpChallenge.__table__.metadata}
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
        phone_verified_at=(
            linked_at if chat_id is not None and unlinked_at is None else None
        ),
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

    return issue_login_otp_in_transaction(
        session,
        settings,
        phone_input=phone_input,
        browser_binding_digest=digest,
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
def test_login_issue_and_new_code_reject_cross_owner_verified_link(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    user = add_user(db_session, "+998900008013")
    other_user = add_user(db_session, "+998900008014")
    other_link = add_link(
        db_session,
        other_user,
        linked_at=NOW - timedelta(minutes=2),
    )

    initial = issue(
        db_session,
        make_settings(m2_test_database),
        phone_input=user.phone,
    )
    challenge = create_pending_challenge(
        db_session,
        user_id=user.id,
        telegram_link_id=other_link.id,
        telegram_linked_at=other_link.linked_at,
        browser_binding_digest="b" * 64,
        now=NOW - timedelta(seconds=61),
    )
    dispatch = create_pending_dispatch(
        db_session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW - timedelta(seconds=61),
    )
    before = (
        count_table(db_session, OtpChallenge),
        count_table(db_session, OtpDispatch),
        count_table(db_session, OtpChallengeEvent),
    )

    new_code = issue_new_login_code_in_transaction(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest="b" * 64,
        locale="uz-Latn",
        now=NOW,
    )
    after = (
        count_table(db_session, OtpChallenge),
        count_table(db_session, OtpDispatch),
        count_table(db_session, OtpChallengeEvent),
    )

    assert initial.outcome is OtpInternalOutcome.OTP_NOT_ELIGIBLE
    assert new_code.outcome is OtpInternalOutcome.OTP_NOT_ELIGIBLE
    assert after == before == (1, 1, 0)
    assert challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert dispatch.status == OtpDispatchStatus.PENDING.value


@pytest.mark.integration
def test_unknown_path_records_phone_ip_limits_runs_dummy_work_and_mutates_no_otp_rows(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    dummy_calls: list[str] = []

    result = coordinate_login_otp_request(
        create_database_session_factory(m2_test_database),
        settings,
        phone_input="+998900008020",
        browser_binding_digest="a" * 64,
        client_ip=ResolvedClientIp("203.0.113.10"),
        locale="uz-Latn",
        now=NOW,
        dummy_work=lambda _key: dummy_calls.append("dummy"),
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
def test_successful_initial_coordinator_uses_closed_distinct_phases_and_scalar_receipt(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session, "+998900008021")
    link = add_link(db_session, user, telegram_chat_id=9_980_100_021)
    user_id = user.id
    link_id = link.id
    linked_at = link.linked_at
    db_session.commit()

    lifecycle = _SessionLifecycle()
    session_factory = _tracking_session_factory(m2_test_database, lifecycle)
    client_ip = ResolvedClientIp("203.0.113.21")
    original_limits = issuance_module.record_login_otp_issue_limits
    original_discovery = issuance_module._discover_login_otp_eligibility
    original_user_limit = issuance_module.record_login_otp_user_issue_limit
    original_domain = issuance_module._request_login_otp_domain

    def tracked_limits(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "phone_ip_rate")
        return original_limits(session, *args, **kwargs)

    def tracked_discovery(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "discovery")
        return original_discovery(session, *args, **kwargs)

    def tracked_user_limit(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "user_rate")
        return original_user_limit(session, *args, **kwargs)

    def tracked_domain(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "domain")
        return original_domain(session, *args, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "record_login_otp_issue_limits",
        tracked_limits,
    )
    monkeypatch.setattr(
        issuance_module,
        "_discover_login_otp_eligibility",
        tracked_discovery,
    )
    monkeypatch.setattr(
        issuance_module,
        "record_login_otp_user_issue_limit",
        tracked_user_limit,
    )
    monkeypatch.setattr(
        issuance_module,
        "_request_login_otp_domain",
        tracked_domain,
    )

    result = coordinate_login_otp_request(
        session_factory,
        settings,
        phone_input="+998 90 000-80-21",
        browser_binding_digest="1" * 64,
        client_ip=client_ip,
        locale="uz-Latn",
        now=NOW,
    )

    assert isinstance(result, CoordinatedOtpIssueResult)
    assert result.accepted is True
    assert result.challenge_id is not None
    assert result.dispatch_id is not None
    assert not hasattr(result, "challenge")
    assert not hasattr(result, "dispatch")
    assert str(result.challenge_id) not in repr(result)
    assert str(result.dispatch_id) not in repr(result)
    assert lifecycle.phases == [
        "phone_ip_rate",
        "discovery",
        "user_rate",
        "domain",
    ]
    assert len(lifecycle.sessions) == 4
    lifecycle.assert_all_closed()

    db_session.expire_all()
    challenge = db_session.get(OtpChallenge, result.challenge_id)
    dispatch = db_session.get(OtpDispatch, result.dispatch_id)
    assert challenge is not None
    assert dispatch is not None
    assert challenge.user_id == user_id
    assert challenge.telegram_link_id == link_id
    assert challenge.telegram_linked_at == linked_at
    assert challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert dispatch.challenge_id == challenge.id
    assert dispatch.status == OtpDispatchStatus.PENDING.value
    assert count_table(db_session, OtpChallengeEvent) == 1
    assert _rate_limit_rows(db_session) == {
        (
            "otp-login-issue:phone",
            hash_rate_limit_key(
                settings,
                "otp-login-issue:phone:+998900008021",
            ),
        ): 1,
        (
            "otp-login-issue:ip",
            hash_rate_limit_key(
                settings,
                f"otp-login-issue:ip:{client_ip.as_hmac_input()}",
            ),
        ): 1,
        (
            "otp-login-issue:user",
            hash_rate_limit_key(
                settings,
                f"otp-login-issue:user:{user_id}",
            ),
        ): 1,
    }


@pytest.mark.integration
def test_initial_coordinator_commits_rate_phases_when_domain_rolls_back(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session, "+998900008022")
    add_link(db_session, user, telegram_chat_id=9_980_100_022)
    user_id = user.id
    db_session.commit()
    session_factory = create_database_session_factory(m2_test_database)
    client_ip = ResolvedClientIp("203.0.113.22")
    domain_entered = Event()
    original_domain = issuance_module._request_login_otp_domain

    def fail_after_domain_write(session: Session, *args: Any, **kwargs: Any):
        domain_entered.set()
        result = original_domain(session, *args, **kwargs)
        assert result.accepted is True
        assert result.challenge is not None
        assert result.dispatch is not None
        raise RuntimeError("injected LOGIN domain failure")

    monkeypatch.setattr(
        issuance_module,
        "_request_login_otp_domain",
        fail_after_domain_write,
    )

    with pytest.raises(RuntimeError, match="injected LOGIN domain failure"):
        coordinate_login_otp_request(
            session_factory,
            settings,
            phone_input=user.phone,
            browser_binding_digest="2" * 64,
            client_ip=client_ip,
            locale="uz-Latn",
            now=NOW,
        )

    assert domain_entered.is_set()
    db_session.expire_all()
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0
    assert _rate_limit_rows(db_session) == {
        (
            "otp-login-issue:phone",
            hash_rate_limit_key(
                settings,
                "otp-login-issue:phone:+998900008022",
            ),
        ): 1,
        (
            "otp-login-issue:ip",
            hash_rate_limit_key(
                settings,
                f"otp-login-issue:ip:{client_ip.as_hmac_input()}",
            ),
        ): 1,
        (
            "otp-login-issue:user",
            hash_rate_limit_key(
                settings,
                f"otp-login-issue:user:{user_id}",
            ),
        ): 1,
    }


@pytest.mark.integration
def test_initial_rate_denial_never_enters_discovery_or_domain(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        m2_test_database,
        otp_login_rate_limit_phone_attempts=1,
    )
    user = add_user(db_session, "+998900008023")
    add_link(db_session, user, telegram_chat_id=9_980_100_023)
    db_session.commit()
    lifecycle = _SessionLifecycle()
    session_factory = _tracking_session_factory(m2_test_database, lifecycle)
    client_ip = ResolvedClientIp("203.0.113.23")
    discovery_entered = Event()
    domain_entered = Event()
    dummy_entered = Event()
    original_limits = issuance_module.record_login_otp_issue_limits
    original_discovery = issuance_module._discover_login_otp_eligibility
    original_domain = issuance_module._request_login_otp_domain

    def tracked_limits(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "phone_ip_rate")
        return original_limits(session, *args, **kwargs)

    def tracked_discovery(session: Session, *args: Any, **kwargs: Any):
        discovery_entered.set()
        return original_discovery(session, *args, **kwargs)

    def tracked_domain(session: Session, *args: Any, **kwargs: Any):
        domain_entered.set()
        return original_domain(session, *args, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "record_login_otp_issue_limits",
        tracked_limits,
    )
    monkeypatch.setattr(
        issuance_module,
        "_discover_login_otp_eligibility",
        tracked_discovery,
    )
    monkeypatch.setattr(
        issuance_module,
        "_request_login_otp_domain",
        tracked_domain,
    )

    result = coordinate_login_otp_request(
        session_factory,
        settings,
        phone_input=user.phone,
        browser_binding_digest="3" * 64,
        client_ip=client_ip,
        locale="uz-Latn",
        now=NOW,
        dummy_work=lambda _key: dummy_entered.set(),
    )

    assert result.outcome is OtpInternalOutcome.RATE_LIMITED
    assert result.challenge_id is None
    assert result.dispatch_id is None
    assert not discovery_entered.is_set()
    assert not domain_entered.is_set()
    assert not dummy_entered.is_set()
    assert lifecycle.phases == ["phone_ip_rate"]
    assert len(lifecycle.sessions) == 1
    lifecycle.assert_all_closed()
    db_session.expire_all()
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0
    assert _rate_limit_rows(db_session) == {
        (
            "otp-login-issue:phone",
            hash_rate_limit_key(
                settings,
                "otp-login-issue:phone:+998900008023",
            ),
        ): 1,
        (
            "otp-login-issue:ip",
            hash_rate_limit_key(
                settings,
                f"otp-login-issue:ip:{client_ip.as_hmac_input()}",
            ),
        ): 1,
    }


@pytest.mark.integration
def test_new_code_coordinator_uses_closed_phases_and_exact_59_60_boundary(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(m2_test_database)
    seed_factory = create_database_session_factory(m2_test_database)
    browser_binding = "4" * 64
    with seed_factory.begin() as seed_session:
        user = add_user(seed_session, "+998900008024")
        add_link(seed_session, user, telegram_chat_id=9_980_100_024)
        initial = issue_login_otp_in_transaction(
            seed_session,
            settings,
            phone_input=user.phone,
            browser_binding_digest=browser_binding,
            locale="uz-Latn",
            now=NOW,
        )
        assert initial.challenge is not None
        assert initial.dispatch is not None
        user_id = user.id
        initial_challenge_id = initial.challenge.id
        initial_dispatch_id = initial.dispatch.id

    original_discovery = issuance_module._discover_login_challenge
    original_limits = issuance_module.record_login_otp_issue_limits
    original_domain = issuance_module._request_new_login_code_domain

    def tracked_discovery(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "new_code_discovery")
        return original_discovery(session, *args, **kwargs)

    def tracked_limits(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "new_code_rate")
        return original_limits(session, *args, **kwargs)

    def tracked_domain(session: Session, *args: Any, **kwargs: Any):
        _mark_tracking_phase(session, "new_code_domain")
        return original_domain(session, *args, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "_discover_login_challenge",
        tracked_discovery,
    )
    monkeypatch.setattr(
        issuance_module,
        "record_login_otp_issue_limits",
        tracked_limits,
    )
    monkeypatch.setattr(
        issuance_module,
        "_request_new_login_code_domain",
        tracked_domain,
    )

    early_lifecycle = _SessionLifecycle()
    early = coordinate_new_login_code_request(
        _tracking_session_factory(m2_test_database, early_lifecycle),
        settings,
        browser_binding_digest=browser_binding,
        client_ip=ResolvedClientIp("203.0.113.24"),
        locale="uz-Latn",
        now=NOW + timedelta(seconds=59),
    )

    assert early.outcome is OtpInternalOutcome.RATE_LIMITED
    assert early.challenge_id is None
    assert early.dispatch_id is None
    assert early_lifecycle.phases == ["new_code_discovery"]
    assert len(early_lifecycle.sessions) == 1
    early_lifecycle.assert_all_closed()

    allowed_lifecycle = _SessionLifecycle()
    client_ip = ResolvedClientIp("203.0.113.24")
    allowed = coordinate_new_login_code_request(
        _tracking_session_factory(m2_test_database, allowed_lifecycle),
        settings,
        browser_binding_digest=browser_binding,
        client_ip=client_ip,
        locale="uz-Latn",
        now=NOW + timedelta(seconds=60),
    )

    assert isinstance(allowed, CoordinatedOtpIssueResult)
    assert allowed.accepted is True
    assert allowed.challenge_id is not None
    assert allowed.dispatch_id is not None
    assert not hasattr(allowed, "challenge")
    assert not hasattr(allowed, "dispatch")
    assert allowed_lifecycle.phases == [
        "new_code_discovery",
        "new_code_rate",
        "new_code_domain",
    ]
    assert len(allowed_lifecycle.sessions) == 3
    allowed_lifecycle.assert_all_closed()

    db_session.expire_all()
    initial_challenge = db_session.get(OtpChallenge, initial_challenge_id)
    initial_dispatch = db_session.get(OtpDispatch, initial_dispatch_id)
    replacement_challenge = db_session.get(OtpChallenge, allowed.challenge_id)
    replacement_dispatch = db_session.get(OtpDispatch, allowed.dispatch_id)
    assert initial_challenge is not None
    assert initial_dispatch is not None
    assert replacement_challenge is not None
    assert replacement_dispatch is not None
    assert initial_challenge.status == OtpChallengeStatus.SUPERSEDED.value
    assert initial_dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert replacement_challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert replacement_dispatch.status == OtpDispatchStatus.PENDING.value
    assert _rate_limit_rows(db_session) == {
        (
            "otp-login-new-code:ip",
            hash_rate_limit_key(
                settings,
                f"otp-login-new-code:ip:{client_ip.as_hmac_input()}",
            ),
        ): 1,
        (
            "otp-login-new-code:user",
            hash_rate_limit_key(
                settings,
                f"otp-login-new-code:user:{user_id}",
            ),
        ): 1,
    }


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
            result = issue_new_login_code_in_transaction(
                session,
                settings,
                browser_binding_digest=digest,
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
    db_session.commit()
    session_factory = create_database_session_factory(m2_test_database)
    first = coordinate_login_otp_request(
        session_factory,
        settings,
        phone_input=user.phone,
        browser_binding_digest="b" * 64,
        client_ip=ResolvedClientIp("203.0.113.10"),
        locale="uz-Latn",
        now=NOW,
    )
    assert first.challenge_id is not None
    assert first.dispatch_id is not None

    blocked = coordinate_login_otp_request(
        session_factory,
        settings,
        phone_input=user.phone,
        browser_binding_digest="c" * 64,
        client_ip=ResolvedClientIp("203.0.113.10"),
        locale="uz-Latn",
        now=NOW + timedelta(seconds=1),
    )

    assert blocked.outcome is OtpInternalOutcome.RATE_LIMITED
    db_session.expire_all()
    first_challenge = db_session.get(OtpChallenge, first.challenge_id)
    first_dispatch = db_session.get(OtpDispatch, first.dispatch_id)
    assert first_challenge is not None
    assert first_dispatch is not None
    assert first_challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert first_dispatch.status == OtpDispatchStatus.PENDING.value
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

    early = issue_new_login_code_in_transaction(
        db_session,
        settings,
        browser_binding_digest="d" * 64,
        locale="uz-Latn",
        now=NOW + timedelta(seconds=59),
    )
    assert early.outcome is OtpInternalOutcome.RATE_LIMITED
    assert first.challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert first.dispatch.status == OtpDispatchStatus.PENDING.value

    allowed = issue_new_login_code_in_transaction(
        db_session,
        settings,
        browser_binding_digest="d" * 64,
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

    chat_identity = VerifiedPrivateTelegramChatIdentity(9_980_100_201)
    sender_identity = TelegramUserIdentity(9_980_100_201)
    bind_start_token_for_contact(
        db_session,
        relink_token.raw_token,
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=NOW + timedelta(seconds=2),
    )
    consume_start_token(
        db_session,
        chat_identity,
        sender_identity,
        sender_identity,
        SensitiveTelegramContactPhone(user.phone),
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=NOW + timedelta(seconds=2),
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
