import logging
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from inspect import getsource
from threading import Barrier, BrokenBarrierError
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.repository as telegram_repository
import app.telegram.service as telegram_service
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.rate_limit import TelegramLinkIssuanceRateLimitResult
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramChatAlreadyLinkedError,
    TelegramLinkLifecycleInternalError,
    TelegramLinkTokenConsumeError,
    TelegramLinkTokenIssueError,
    bind_start_token_for_contact,
    consume_start_token,
)
from app.telegram.token import (
    TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
    RawTelegramLinkToken,
    hash_telegram_link_token,
)
from tests.telegram_issue_helpers import (
    issue_link_token_in_one_test_transaction as issue_link_token,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-conflicts"
CONTACT_BINDING_KEY = SecretStr(TEST_RATE_LIMIT_HMAC_KEY)
_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 15


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        telegram_link_rate_limit_user_attempts=10,
        telegram_link_rate_limit_phone_attempts=10,
        telegram_link_rate_limit_ip_attempts=20,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def add_active_link(
    session: Session,
    user: User,
    *,
    telegram_chat_id: int,
    linked_at: datetime,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        phone_verified_at=linked_at,
        updated_at=linked_at,
    )
    session.add(link)
    session.flush()
    return link


def add_tombstone_link(
    session: Session,
    user: User,
    *,
    linked_at: datetime,
    unlinked_at: datetime,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=None,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        updated_at=unlinked_at,
    )
    session.add(link)
    session.flush()
    return link


def add_token(
    session: Session,
    user: User,
    *,
    raw_token: str,
    created_at: datetime,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS),
    )
    session.add(token)
    session.flush()
    return token


def consume_raw(
    session: Session,
    *,
    raw_token: str,
    telegram_chat_id: int,
    now: datetime,
):
    raw = RawTelegramLinkToken(raw_token)
    token_hash = hash_telegram_link_token(raw)
    phone = session.scalar(
        select(User.phone)
        .join(TelegramLinkToken, TelegramLinkToken.user_id == User.id)
        .where(TelegramLinkToken.token_hash == token_hash)
    )
    assert phone is not None
    chat_identity = VerifiedPrivateTelegramChatIdentity(telegram_chat_id)
    sender_identity = TelegramUserIdentity(telegram_chat_id)
    bind_start_token_for_contact(
        session,
        raw,
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now,
    )
    return consume_start_token(
        session,
        chat_identity,
        sender_identity,
        sender_identity,
        SensitiveTelegramContactPhone(phone),
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now,
    )


def token_state(
    session: Session,
    token_id: UUID,
) -> tuple[datetime | None, datetime | None]:
    token = session.get(TelegramLinkToken, token_id)
    assert token is not None
    return (token.consumed_at, token.invalidated_at)


def link_state(
    session: Session,
    link_id: UUID,
) -> tuple[int | None, datetime, datetime | None, datetime]:
    link = session.get(TelegramLink, link_id)
    assert link is not None
    return (
        link.telegram_chat_id,
        link.linked_at,
        link.unlinked_at,
        link.updated_at,
    )


def count_events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TelegramLinkEvent)) or 0


def stored_conflict_text(session: Session) -> str:
    rows = session.execute(
        text(
            "SELECT 'telegram_links', COALESCE(telegram_chat_id::text, ''), "
            "linked_at::text, COALESCE(unlinked_at::text, ''), updated_at::text "
            "FROM telegram_links "
            "UNION ALL "
            "SELECT 'telegram_link_tokens', token_hash, created_at::text, "
            "COALESCE(consumed_at::text, ''), COALESCE(invalidated_at::text, '') "
            "FROM telegram_link_tokens "
            "UNION ALL "
            "SELECT 'telegram_link_events', action, occurred_at::text, '', '' "
            "FROM telegram_link_events"
        )
    ).all()
    return "|".join(str(value) for row in rows for value in row)


def assert_no_raw_detail(text_value: str, *forbidden_values: str) -> None:
    forbidden_fragments = (
        "IntegrityError",
        "Traceback",
        "INSERT INTO",
        "UPDATE ",
        "SELECT ",
        "uq_telegram_links_active_chat_id",
        "uq_telegram_links_user_id",
        "uq_telegram_link_tokens_token_hash",
        "uq_telegram_link_tokens_one_outstanding_per_user",
        "ck_telegram_link_events_action_allowed",
    )
    for fragment in (*forbidden_fragments, *forbidden_values):
        assert fragment not in text_value


def assert_same_session_select_and_insert(
    session: Session,
    *,
    phone: str,
) -> UUID:
    assert session.scalar(select(1)) == 1
    user = add_user(session, phone)
    assert user.id is not None
    assert session.scalar(select(1)) == 1
    return user.id


def test_expected_conflict_paths_use_savepoints_without_service_rollback() -> None:
    assert "begin_nested" in getsource(
        telegram_repository.invalidate_and_insert_telegram_link_token
    )
    assert "begin_nested" in getsource(
        telegram_service._mutate_link_with_collision_recovery
    )
    assert "rollback(" not in getsource(issue_link_token)
    assert "rollback(" not in getsource(consume_start_token)
    assert "rollback(" not in getsource(
        telegram_service._mutate_link_with_collision_recovery
    )


@pytest.mark.integration
def test_duplicate_token_hash_conflict_maps_safe_and_session_recovers(
    m2_test_database: Engine,
) -> None:
    raw_token = "duplicate_hash_conflict_token"
    now = datetime(2026, 7, 25, 13, 40, tzinfo=UTC)
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        existing_user = add_user(session, "+998900019001")
        candidate_user = add_user(session, "+998900019002")
        existing = add_token(
            session,
            existing_user,
            raw_token=raw_token,
            created_at=now - timedelta(minutes=1),
        )
        existing_token_id = existing.id
        candidate_user_id = candidate_user.id
        session.commit()

        current_user = session.get(User, candidate_user_id)
        assert current_user is not None

        def token_generator(byte_count: int) -> str:
            assert byte_count == TELEGRAM_LINK_TOKEN_ENTROPY_BYTES
            return raw_token

        with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
            issue_link_token(
                session,
                make_settings(m2_test_database),
                current_user,
                ResolvedClientIp("203.0.113.191"),
                now,
                token_generator=token_generator,
            )
        error_text = (
            f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
        )
        continuation_user_id = assert_same_session_select_and_insert(
            session,
            phone="+998900019003",
        )

        assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
        assert_no_raw_detail(error_text, raw_token)
        assert token_state(session, existing_token_id) == (None, None)
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramLinkToken)
                .where(TelegramLinkToken.user_id == candidate_user_id)
            )
            == 0
        )
        assert session.get(User, continuation_user_id) is not None
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_active_chat_unique_conflict_maps_safe_and_session_recovers(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "active_chat_unique_conflict_token"
    chat_id = 19_101
    linked_at = datetime(2026, 7, 25, 13, 50, tzinfo=UTC)
    consume_at = linked_at + timedelta(minutes=2)
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        owner = add_user(session, "+998900019101")
        candidate = add_user(session, "+998900019102")
        add_active_link(
            session,
            owner,
            telegram_chat_id=chat_id,
            linked_at=linked_at,
        )
        token = add_token(
            session,
            candidate,
            raw_token=raw_token,
            created_at=linked_at + timedelta(minutes=1),
        )
        token_id = token.id
        session.commit()

        monkeypatch.setattr(
            telegram_service,
            "get_other_active_telegram_link_by_chat_identity_for_update",
            lambda *_args, **_kwargs: None,
        )
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TelegramChatAlreadyLinkedError) as exc_info:
                consume_raw(
                    session,
                    raw_token=raw_token,
                    telegram_chat_id=chat_id,
                    now=consume_at,
                )
        error_text = (
            f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
        )
        continuation_user_id = assert_same_session_select_and_insert(
            session,
            phone="+998900019103",
        )

        assert exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
        assert token_state(session, token_id) == (None, None)
        assert count_events(session) == 0
        assert session.get(User, continuation_user_id) is not None
        assert_no_raw_detail(error_text, raw_token, str(chat_id), owner.phone)
        assert_no_raw_detail(caplog.text, raw_token, str(chat_id), owner.phone)
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_user_link_unique_conflict_maps_safe_and_session_recovers(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "user_link_unique_conflict_token"
    linked_at = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)
    tombstone_at = linked_at + timedelta(minutes=1)
    consume_at = tombstone_at + timedelta(minutes=1)
    chat_id = 19_201
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = add_user(session, "+998900019201")
        link = add_tombstone_link(
            session,
            user,
            linked_at=linked_at,
            unlinked_at=tombstone_at,
        )
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=tombstone_at,
        )
        link_id = link.id
        token_id = token.id
        session.commit()

        monkeypatch.setattr(
            telegram_service,
            "lock_telegram_link_change_set",
            lambda *_args, **_kwargs: (),
        )
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
                consume_raw(
                    session,
                    raw_token=raw_token,
                    telegram_chat_id=chat_id,
                    now=consume_at,
                )
        error_text = (
            f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
        )
        continuation_user_id = assert_same_session_select_and_insert(
            session,
            phone="+998900019202",
        )

        assert exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
        assert link_state(session, link_id) == (
            None,
            linked_at,
            tombstone_at,
            tombstone_at,
        )
        assert token_state(session, token_id) == (None, None)
        assert count_events(session) == 0
        assert session.get(User, continuation_user_id) is not None
        assert_no_raw_detail(error_text, raw_token, str(chat_id))
        assert_no_raw_detail(caplog.text, raw_token, str(chat_id))
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_parallel_issue_partial_unique_conflict_session_recovers_with_insert(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = add_user(setup_session, "+998900019301")
        user_id = user.id
        setup_session.commit()
    finally:
        setup_session.close()

    now = datetime(2026, 7, 25, 14, 10, tzinfo=UTC)
    client_ip = ResolvedClientIp("203.0.113.193")
    raw_by_label = {
        "first": "partial_unique_parallel_issue_first",
        "second": "partial_unique_parallel_issue_second",
    }
    start_barrier = Barrier(2)
    empty_outstanding_barrier = Barrier(2)
    original_get_outstanding = (
        telegram_repository.get_outstanding_telegram_link_token_for_update
    )

    def allow_rate_limit(*_args, **_kwargs) -> TelegramLinkIssuanceRateLimitResult:
        return TelegramLinkIssuanceRateLimitResult(allowed=True)

    def synchronized_get_outstanding(
        session: Session,
        current_user: User,
    ) -> TelegramLinkToken | None:
        token = original_get_outstanding(session, current_user)
        if token is None:
            empty_outstanding_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return token

    def worker(label: str) -> tuple[str, str, ErrorCode | None, bool, str]:
        session = session_factory()
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            if current_user is None:
                return (label, "unexpected", None, False, "MissingUser")

            def token_generator(byte_count: int) -> str:
                assert byte_count == TELEGRAM_LINK_TOKEN_ENTROPY_BYTES
                return raw_by_label[label]

            try:
                issued = issue_link_token(
                    session,
                    make_settings(m2_test_database),
                    current_user,
                    client_ip,
                    now,
                    token_generator=token_generator,
                )
            except TelegramLinkTokenIssueError as exc:
                error_text = f"{exc!r} {exc} {exc.public_error}"
                assert_no_raw_detail(error_text, *raw_by_label.values())
                assert_same_session_select_and_insert(
                    session,
                    phone=("+998900019302" if label == "first" else "+998900019303"),
                )
                session.commit()
                return (label, "domain_error", exc.error_code, True, "")

            assert_same_session_select_and_insert(
                session,
                phone="+998900019304" if label == "first" else "+998900019305",
            )
            token_hash = hash_telegram_link_token(issued.raw_token)
            session.commit()
            return (label, "issued", None, True, token_hash)
        except BrokenBarrierError:
            session.rollback()
            return (label, "unexpected", None, False, "BrokenBarrierError")
        except Exception as exc:
            session.rollback()
            return (label, "unexpected", None, False, type(exc).__name__)
        finally:
            session.close()

    monkeypatch.setattr(
        telegram_service,
        "record_telegram_link_issuance_attempt",
        allow_rate_limit,
    )
    monkeypatch.setattr(
        telegram_repository,
        "get_outstanding_telegram_link_token_for_update",
        synchronized_get_outstanding,
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(worker, label) for label in raw_by_label]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            empty_outstanding_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel issue conflict timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    final_session = session_factory()
    try:
        token_rows = final_session.scalars(
            select(TelegramLinkToken).where(TelegramLinkToken.user_id == user_id)
        ).all()
        outstanding_rows = [
            token
            for token in token_rows
            if token.consumed_at is None and token.invalidated_at is None
        ]
        stored_text = stored_conflict_text(final_session)
    finally:
        final_session.close()

    issued = [outcome for outcome in outcomes if outcome[1] == "issued"]
    domain_errors = [outcome for outcome in outcomes if outcome[1] == "domain_error"]
    unexpected = [outcome for outcome in outcomes if outcome[1] == "unexpected"]
    outcome_text = " ".join(str(outcome) for outcome in outcomes)

    assert unexpected == []
    assert len(issued) == 1
    assert len(domain_errors) == 1
    assert domain_errors[0][2] is ErrorCode.RATE_LIMITED
    assert all(outcome[3] is True for outcome in outcomes)
    assert len(outstanding_rows) == 1
    assert len(token_rows) == 1
    assert_no_raw_detail(outcome_text, *raw_by_label.values())
    assert_no_raw_detail(caplog.text, *raw_by_label.values())
    for raw_token in raw_by_label.values():
        assert raw_token not in stored_text


@pytest.mark.integration
def test_event_action_constraint_failure_maps_internal_and_recovers_after_rollback(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "event_action_constraint_failure_token"
    raw_database_detail = (
        "raw event action constraint detail "
        "ck_telegram_link_events_action_allowed "
        "event_action_constraint_failure_token"
    )
    issued_at = datetime(2026, 7, 25, 14, 20, tzinfo=UTC)
    consume_at = issued_at + timedelta(minutes=1)
    chat_id = 19_401
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = add_user(session, "+998900019401")
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=issued_at,
        )
        user_id = user.id
        token_id = token.id
        session.commit()

        def append_bad_action_event(
            session: Session,
            user_id: UUID,
            _action,
            occurred_at: datetime,
        ) -> TelegramLinkEvent:
            event = TelegramLinkEvent(
                user_id=user_id,
                action="bad-action",
                occurred_at=occurred_at,
            )
            session.add(event)
            session.flush()
            return event

        monkeypatch.setattr(
            telegram_service,
            "append_telegram_link_event",
            append_bad_action_event,
        )
        with pytest.raises(TelegramLinkLifecycleInternalError) as exc_info:
            consume_raw(
                session,
                raw_token=raw_token,
                telegram_chat_id=chat_id,
                now=consume_at,
            )
        error_text = f"{exc_info.value!r} {exc_info.value}"

        assert str(exc_info.value) == "Telegram link lifecycle transition failed"
        assert exc_info.value.__cause__ is None
        assert_no_raw_detail(error_text, raw_database_detail, raw_token, str(chat_id))
        session.rollback()
        continuation_user_id = assert_same_session_select_and_insert(
            session,
            phone="+998900019402",
        )
        session.rollback()
        assert session.get(User, continuation_user_id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramLink)
                .where(TelegramLink.user_id == user_id)
            )
            == 0
        )
        assert token_state(session, token_id) == (None, None)
        assert count_events(session) == 0
    finally:
        session.rollback()
        session.close()
