import inspect
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.otp.repository as otp_repository
from app.auth.models import User
from app.db import create_database_session_factory
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDeliveryFailureCode,
    OtpDispatchStatus,
    OtpPurpose,
)
from app.otp.models import (
    OtpChallenge,
    OtpChallengeEvent,
    OtpDispatch,
    OtpDispatcherState,
)
from app.otp.repository import (
    OtpChallengeInsertConflict,
    OtpDispatchInsertConflict,
    OtpRepositoryStateError,
    activate_challenge,
    append_challenge_event,
    claim_next_pending_dispatch_for_update,
    consume_challenge,
    create_pending_challenge,
    create_pending_dispatch,
    get_or_create_dispatcher_state_for_update,
    increment_challenge_failed_attempts,
    load_outstanding_challenge_by_browser_for_update,
    load_outstanding_challenge_by_user_for_update,
    load_stale_prepared_dispatches_for_update,
    mark_dispatch_failed,
    mark_dispatch_prepared,
    mark_dispatch_sent,
    mark_stale_prepared_dispatch_unknown,
    purge_terminal_otp_records,
)
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
VALID_DIGEST = "a" * 64
VALID_MAC = "b" * 64


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False
        self.close_called = False

    def add(self, *args, **kwargs):
        return self.session.add(*args, **kwargs)

    def flush(self, *args, **kwargs):
        return self.session.flush(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

    def scalars(self, *args, **kwargs):
        return self.session.scalars(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True

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


def add_user(session: Session, phone: str = "+998900007001") -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def add_link(
    session: Session,
    user: User,
    *,
    linked_at: datetime = NOW,
    telegram_chat_id: int | None = None,
) -> TelegramLink:
    if telegram_chat_id is None:
        link_count = session.scalar(select(func.count()).select_from(TelegramLink)) or 0
        telegram_chat_id = 9_980_000_001 + link_count
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        updated_at=linked_at,
    )
    session.add(link)
    session.flush()
    return link


def create_active_challenge(
    session: Session,
    user: User,
    link: TelegramLink,
    *,
    digest: str = VALID_DIGEST,
    now: datetime = NOW,
) -> OtpChallenge:
    challenge = create_pending_challenge(
        session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=digest,
        now=now,
    )
    return activate_challenge(
        session,
        challenge=challenge,
        code_mac=VALID_MAC,
        activated_at=now + timedelta(seconds=1),
        expires_at=now + timedelta(minutes=3),
    )


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def seed_committed_user_and_link(engine: Engine) -> tuple[UUID, UUID, datetime]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, "+998900007099")
        link = add_link(session, user)
        user_id = user.id
        link_id = link.id
        linked_at = link.linked_at
        session.commit()
        return user_id, link_id, linked_at
    finally:
        session.close()


def test_otp_repository_public_api_is_caller_owned_and_narrow() -> None:
    source = inspect.getsource(otp_repository)

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "httpx" not in source
    assert "Request" not in source
    assert "telegram_bot_token" not in source
    assert "raw_otp" not in source
    assert "raw_code" not in source
    assert "phone" not in source
    assert "telegram_chat_id" not in source
    assert "message_text" not in source
    assert "payload" not in source
    assert "logger" not in source
    assert "print(" not in source

    for name, value in inspect.getmembers(otp_repository):
        if name.startswith("_") or not callable(value):
            continue
        if getattr(value, "__module__", None) != otp_repository.__name__:
            continue
        assert not name.startswith(("update", "delete", "replace"))

    for function in (
        create_pending_challenge,
        load_outstanding_challenge_by_user_for_update,
        create_pending_dispatch,
        append_challenge_event,
        purge_terminal_otp_records,
    ):
        parameters = inspect.signature(function).parameters
        assert next(iter(parameters)) == "session"
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for name, parameter in list(parameters.items())[1:]
            if name != "self"
        )


@pytest.mark.integration
def test_challenge_repository_lifecycle_locks_and_terminal_guard(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)
    challenge = create_pending_challenge(
        db_session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=VALID_DIGEST,
        now=NOW,
    )

    assert (
        load_outstanding_challenge_by_user_for_update(
            db_session,
            user_id=user.id,
            purpose=OtpPurpose.LOGIN,
        )
        is challenge
    )
    assert (
        load_outstanding_challenge_by_browser_for_update(
            db_session,
            browser_binding_digest=VALID_DIGEST,
            purpose=OtpPurpose.LOGIN,
        )
        is challenge
    )

    activate_challenge(
        db_session,
        challenge=challenge,
        code_mac=VALID_MAC,
        activated_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=3),
    )
    increment_challenge_failed_attempts(
        db_session,
        challenge=challenge,
        now=NOW + timedelta(seconds=10),
        max_attempts=2,
    )
    increment_challenge_failed_attempts(
        db_session,
        challenge=challenge,
        now=NOW + timedelta(seconds=11),
        max_attempts=2,
    )

    assert challenge.status == OtpChallengeStatus.BURNED.value
    assert challenge.failed_attempts == 2
    assert challenge.terminal_at == NOW + timedelta(seconds=11)
    assert (
        load_outstanding_challenge_by_user_for_update(
            db_session,
            user_id=user.id,
            purpose=OtpPurpose.LOGIN,
        )
        is None
    )
    with pytest.raises(OtpRepositoryStateError):
        activate_challenge(
            db_session,
            challenge=challenge,
            code_mac=VALID_MAC,
            activated_at=NOW + timedelta(seconds=12),
            expires_at=NOW + timedelta(minutes=4),
        )


@pytest.mark.integration
def test_challenge_uniqueness_conflict_is_sanitized_and_session_remains_usable(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)
    create_pending_challenge(
        db_session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=VALID_DIGEST,
        now=NOW,
    )

    with pytest.raises(OtpChallengeInsertConflict):
        create_pending_challenge(
            db_session,
            user_id=user.id,
            telegram_link_id=link.id,
            telegram_linked_at=link.linked_at,
            browser_binding_digest="c" * 64,
            now=NOW + timedelta(seconds=1),
        )

    assert count_table(db_session, OtpChallenge) == 1
    assert db_session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.integration
def test_parallel_outstanding_inserts_have_exactly_one_winner(
    m2_test_database: Engine,
) -> None:
    user_id, link_id, linked_at = seed_committed_user_and_link(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    barrier = Barrier(2)

    def insert_candidate(digest: str) -> str:
        session = session_factory()
        try:
            barrier.wait()
            try:
                create_pending_challenge(
                    session,
                    user_id=user_id,
                    telegram_link_id=link_id,
                    telegram_linked_at=linked_at,
                    browser_binding_digest=digest,
                    now=NOW,
                )
                session.commit()
                return "inserted"
            except OtpChallengeInsertConflict:
                assert session.scalar(select(func.count()).select_from(User)) == 1
                session.rollback()
                return "conflict"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert_candidate, ("d" * 64, "e" * 64)))

    assert sorted(results) == ["conflict", "inserted"]
    session = session_factory()
    try:
        assert count_table(session, OtpChallenge) == 1
    finally:
        session.close()


@pytest.mark.integration
def test_challenge_invalid_state_combinations_are_rejected_by_database(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)

    invalid_rows = [
        OtpChallenge(
            purpose=OtpPurpose.LOGIN.value,
            browser_binding_digest=VALID_DIGEST,
            code_mac=VALID_MAC,
            status=OtpChallengeStatus.PENDING_DISPATCH.value,
            created_at=NOW,
            updated_at=NOW,
        ),
        OtpChallenge(
            user_id=user.id,
            telegram_link_id=link.id,
            telegram_linked_at=link.linked_at,
            purpose=OtpPurpose.LOGIN.value,
            browser_binding_digest="c" * 64,
            status=OtpChallengeStatus.ACTIVE.value,
            activated_at=NOW,
            expires_at=NOW + timedelta(minutes=3),
            created_at=NOW,
            updated_at=NOW,
        ),
        OtpChallenge(
            purpose=OtpPurpose.LOGIN.value,
            browser_binding_digest="d" * 64,
            status=OtpChallengeStatus.PENDING_DISPATCH.value,
            failed_attempts=11,
            created_at=NOW,
            updated_at=NOW,
        ),
    ]

    for row in invalid_rows:
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.add(row)
                db_session.flush()
    assert count_table(db_session, OtpChallenge) == 0


@pytest.mark.integration
def test_dispatch_repository_claim_prepare_results_and_stale_recovery(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)
    challenge = create_active_challenge(db_session, user, link)
    dispatch = create_pending_dispatch(
        db_session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    with pytest.raises(OtpDispatchInsertConflict):
        create_pending_dispatch(
            db_session,
            challenge_id=challenge.id,
            locale="ru",
            now=NOW,
        )

    claimed = claim_next_pending_dispatch_for_update(
        db_session,
        now=NOW + timedelta(seconds=2),
    )
    assert claimed is dispatch
    assert dispatch.status == OtpDispatchStatus.PENDING.value
    mark_dispatch_prepared(
        db_session,
        dispatch=dispatch,
        challenge=challenge,
        now=NOW + timedelta(seconds=3),
    )
    mark_dispatch_sent(
        db_session,
        dispatch=dispatch,
        now=NOW + timedelta(seconds=4),
    )
    assert dispatch.status == OtpDispatchStatus.SENT.value
    assert (
        claim_next_pending_dispatch_for_update(
            db_session,
            now=NOW + timedelta(seconds=5),
        )
        is None
    )

    second_user = add_user(db_session, "+998900007002")
    second_link = add_link(db_session, second_user, linked_at=NOW)
    stale_challenge = create_active_challenge(
        db_session,
        second_user,
        second_link,
        digest="f" * 64,
    )
    stale_dispatch = create_pending_dispatch(
        db_session,
        challenge_id=stale_challenge.id,
        locale="ru",
        now=NOW,
    )
    claim_next_pending_dispatch_for_update(
        db_session,
        now=NOW + timedelta(seconds=6),
    )
    mark_dispatch_prepared(
        db_session,
        dispatch=stale_dispatch,
        challenge=stale_challenge,
        now=NOW + timedelta(seconds=7),
    )

    stale_rows = load_stale_prepared_dispatches_for_update(
        db_session,
        stale_before=NOW + timedelta(minutes=2),
        limit=10,
    )
    assert stale_rows == [stale_dispatch]
    mark_stale_prepared_dispatch_unknown(
        db_session,
        dispatch=stale_dispatch,
        now=NOW + timedelta(minutes=2),
    )
    assert stale_dispatch.status == OtpDispatchStatus.UNKNOWN.value
    assert stale_dispatch.failure_code == "OTP_DISPATCH_STALE_PREPARED"

    failed_user = add_user(db_session, "+998900007004")
    failed_link = add_link(db_session, failed_user)
    failed_challenge = create_active_challenge(
        db_session,
        failed_user,
        failed_link,
        digest="3" * 64,
    )
    failed_dispatch = create_pending_dispatch(
        db_session,
        challenge_id=failed_challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    claim_next_pending_dispatch_for_update(
        db_session,
        now=NOW + timedelta(seconds=8),
    )
    mark_dispatch_prepared(
        db_session,
        dispatch=failed_dispatch,
        challenge=failed_challenge,
        now=NOW + timedelta(seconds=9),
    )
    mark_dispatch_failed(
        db_session,
        dispatch=failed_dispatch,
        failure_code=OtpDeliveryFailureCode.TELEGRAM_PROTOCOL,
        now=NOW + timedelta(seconds=10),
    )
    assert failed_dispatch.status == OtpDispatchStatus.FAILED.value
    assert (
        failed_dispatch.failure_code == OtpDeliveryFailureCode.TELEGRAM_PROTOCOL.value
    )


@pytest.mark.integration
def test_prepared_dispatch_is_not_reclaimed_but_pending_claim_can_be_reclaimed(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = add_user(session)
        link = add_link(session, user)
        challenge = create_active_challenge(session, user, link)
        dispatch = create_pending_dispatch(
            session,
            challenge_id=challenge.id,
            locale="uz-Latn",
            now=NOW,
        )
        session.commit()
        dispatch_id = dispatch.id
        challenge_id = challenge.id
    finally:
        session.close()

    first_session = session_factory()
    try:
        claimed = claim_next_pending_dispatch_for_update(
            first_session,
            now=NOW + timedelta(seconds=1),
        )
        assert claimed is not None
        first_session.commit()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        reclaimed = claim_next_pending_dispatch_for_update(
            second_session,
            now=NOW + timedelta(seconds=70),
            claim_stale_before=NOW + timedelta(seconds=60),
        )
        assert reclaimed is not None
        assert reclaimed.id == dispatch_id
        second_session.commit()
    finally:
        second_session.close()

    third_session = session_factory()
    try:
        dispatch = third_session.get(OtpDispatch, dispatch_id, with_for_update=True)
        challenge = third_session.get(OtpChallenge, challenge_id, with_for_update=True)
        assert dispatch is not None
        assert challenge is not None
        mark_dispatch_prepared(
            third_session,
            dispatch=dispatch,
            challenge=challenge,
            now=NOW + timedelta(seconds=80),
        )
        third_session.commit()
    finally:
        third_session.close()

    final_session = session_factory()
    try:
        assert (
            claim_next_pending_dispatch_for_update(
                final_session,
                now=NOW + timedelta(seconds=90),
                claim_stale_before=NOW + timedelta(seconds=80),
            )
            is None
        )
    finally:
        final_session.close()


@pytest.mark.integration
def test_dispatch_invalid_state_combinations_are_rejected_by_database(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)
    challenge = create_active_challenge(db_session, user, link)

    invalid_rows = [
        OtpDispatch(
            challenge_id=challenge.id,
            status=OtpDispatchStatus.PREPARED.value,
            locale="uz-Latn",
            prepared_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ),
        OtpDispatch(
            challenge_id=challenge.id,
            status=OtpDispatchStatus.FAILED.value,
            locale="uz-Latn",
            claimed_at=NOW,
            prepared_at=NOW,
            terminal_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ),
    ]

    for row in invalid_rows:
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.add(row)
                db_session.flush()
    assert count_table(db_session, OtpDispatch) == 0


@pytest.mark.integration
def test_append_events_are_safe_and_database_enforces_event_checks(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    event = append_challenge_event(
        db_session,
        challenge_id=uuid4(),
        user_id=user.id,
        action=OtpChallengeEventAction.DISPATCH_RESULT,
        safe_code=OtpDeliveryFailureCode.TELEGRAM_UNKNOWN.value,
        occurred_at=NOW,
    )

    assert event.action == OtpChallengeEventAction.DISPATCH_RESULT.value
    assert event.safe_code == OtpDeliveryFailureCode.TELEGRAM_UNKNOWN.value
    assert count_table(db_session, OtpChallengeEvent) == 1

    with pytest.raises(ValueError):
        append_challenge_event(
            db_session,
            challenge_id=uuid4(),
            user_id=user.id,
            action="UNKNOWN_ACTION",
            occurred_at=NOW,
        )
    with pytest.raises(ValueError):
        append_challenge_event(
            db_session,
            challenge_id=uuid4(),
            user_id=user.id,
            action=OtpChallengeEventAction.VERIFY_FAILED,
            safe_code="RANDOM_SAFE_CODE",
            occurred_at=NOW,
        )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                OtpChallengeEvent(
                    challenge_id=uuid4(),
                    user_id=user.id,
                    action="BAD",
                    occurred_at=NOW,
                )
            )
            db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                OtpChallengeEvent(
                    challenge_id=uuid4(),
                    user_id=user.id,
                    action=OtpChallengeEventAction.VERIFY_FAILED.value,
                    safe_code="lowercase",
                    occurred_at=NOW,
                )
            )
            db_session.flush()
    assert count_table(db_session, OtpChallengeEvent) == 1


@pytest.mark.integration
def test_dispatcher_state_is_singleton_and_heartbeat_is_idempotent(
    db_session: Session,
) -> None:
    state = get_or_create_dispatcher_state_for_update(db_session, now=NOW)
    again = otp_repository.mark_dispatcher_heartbeat(
        db_session,
        now=NOW + timedelta(seconds=10),
        ready=True,
    )

    assert state is again
    assert again.id == 1
    assert again.heartbeat_at == NOW + timedelta(seconds=10)
    assert again.ready_at == NOW + timedelta(seconds=10)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                OtpDispatcherState(
                    id=2,
                    heartbeat_at=NOW,
                    ready_at=NOW,
                    updated_at=NOW,
                )
            )
            db_session.flush()


@pytest.mark.integration
def test_retention_purge_uses_exact_cutoffs_and_keeps_outstanding_and_state(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)
    terminal_time = NOW - timedelta(days=30)
    event_time = NOW - timedelta(days=90)
    old_challenge = create_active_challenge(
        db_session,
        user,
        link,
        digest="1" * 64,
        now=terminal_time - timedelta(minutes=1),
    )
    old_dispatch = create_pending_dispatch(
        db_session,
        challenge_id=old_challenge.id,
        locale="uz-Latn",
        now=terminal_time - timedelta(minutes=1),
    )
    claim_next_pending_dispatch_for_update(
        db_session,
        now=terminal_time - timedelta(seconds=5),
    )
    mark_dispatch_prepared(
        db_session,
        dispatch=old_dispatch,
        challenge=old_challenge,
        now=terminal_time - timedelta(seconds=4),
    )
    mark_dispatch_sent(db_session, dispatch=old_dispatch, now=terminal_time)
    consume_challenge(db_session, challenge=old_challenge, now=terminal_time)
    append_challenge_event(
        db_session,
        challenge_id=old_challenge.id,
        user_id=user.id,
        action=OtpChallengeEventAction.CONSUMED,
        occurred_at=event_time,
    )

    fresh_user = add_user(db_session, "+998900007003")
    fresh_link = add_link(db_session, fresh_user)
    fresh_challenge = create_pending_challenge(
        db_session,
        user_id=fresh_user.id,
        telegram_link_id=fresh_link.id,
        telegram_linked_at=fresh_link.linked_at,
        browser_binding_digest="2" * 64,
        now=NOW,
    )
    fresh_event = append_challenge_event(
        db_session,
        challenge_id=fresh_challenge.id,
        user_id=fresh_user.id,
        action=OtpChallengeEventAction.ISSUED,
        occurred_at=NOW - timedelta(days=89, hours=23),
    )
    get_or_create_dispatcher_state_for_update(db_session, now=NOW)
    db_session.flush()

    result = purge_terminal_otp_records(db_session, now=NOW, batch_size=5000)

    assert result.dispatches_deleted == 1
    assert result.challenges_deleted == 1
    assert result.events_deleted == 1
    assert db_session.get(OtpDispatch, old_dispatch.id) is None
    assert db_session.get(OtpChallenge, old_challenge.id) is None
    assert db_session.get(OtpChallenge, fresh_challenge.id) is not None
    assert db_session.get(OtpChallengeEvent, fresh_event.id) is not None
    assert count_table(db_session, OtpDispatcherState) == 1


def test_purge_rejects_invalid_batch_and_retention_values(db_session: Session) -> None:
    with pytest.raises(ValueError, match="between 1 and 5000"):
        purge_terminal_otp_records(db_session, now=NOW, batch_size=0)
    with pytest.raises(ValueError, match="between 1 and 5000"):
        purge_terminal_otp_records(db_session, now=NOW, batch_size=5001)
    with pytest.raises(ValueError, match="retention days"):
        purge_terminal_otp_records(
            db_session,
            now=NOW,
            batch_size=1,
            terminal_retention_days=0,
        )


@pytest.mark.integration
def test_repository_primitives_do_not_commit_or_close_caller_session(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    link = add_link(db_session, user)
    session_spy = SessionSpy(db_session)
    challenge = create_pending_challenge(
        session_spy,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=VALID_DIGEST,
        now=NOW,
    )
    append_challenge_event(
        session_spy,
        challenge_id=challenge.id,
        user_id=user.id,
        action=OtpChallengeEventAction.ISSUED,
        occurred_at=NOW,
    )

    assert session_spy.commit_called is False
    assert session_spy.rollback_called is False
    assert session_spy.close_called is False
