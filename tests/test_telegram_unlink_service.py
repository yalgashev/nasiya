import logging
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from threading import Barrier, BrokenBarrierError, Event

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.service as telegram_service
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenConsumeError,
    TelegramLinkTokenIssueError,
    TelegramStartTokenConsumeOutcome,
    UnlinkedTelegramLink,
    bind_start_token_for_contact,
    consume_start_token,
)
from app.telegram.service import (
    unlink as unlink_telegram,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token
from tests.telegram_issue_helpers import (
    issue_relink_token_in_one_test_transaction as issue_relink_token,
)

_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 15
_R05_RATE_LIMIT_HMAC_KEY = "r05-rate-limit-hmac-key-at-least-32-characters"
_CONTACT_BINDING_KEY = SecretStr(_R05_RATE_LIMIT_HMAC_KEY)


@dataclass(frozen=True, repr=False)
class ParallelUnlinkOutcome:
    label: str
    kind: str
    error_code: ErrorCode | None = None
    invalidated_token_count: int | None = None
    unlinked_at: datetime | None = None
    session_usable: bool = False
    exception_class: str | None = None

    def __repr__(self) -> str:
        return (
            "ParallelUnlinkOutcome("
            f"label={self.label!r}, kind={self.kind!r}, "
            f"error_code={self.error_code}, "
            f"invalidated_token_count={self.invalidated_token_count}, "
            f"session_usable={self.session_usable}, "
            f"exception_class={self.exception_class!r}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class ParallelLifecycleRaceOutcome:
    label: str
    kind: str
    error_code: ErrorCode | None = None
    consume_outcome: TelegramStartTokenConsumeOutcome | None = None
    invalidated_token_count: int | None = None
    unlinked_at: datetime | None = None
    event_action: str | None = None
    event_at: datetime | None = None
    session_usable: bool = False
    exception_class: str | None = None

    def __repr__(self) -> str:
        return (
            "ParallelLifecycleRaceOutcome("
            f"label={self.label!r}, kind={self.kind!r}, "
            f"error_code={self.error_code}, "
            f"consume_outcome={self.consume_outcome}, "
            f"invalidated_token_count={self.invalidated_token_count}, "
            f"session_usable={self.session_usable}, "
            f"exception_class={self.exception_class!r}"
            ")"
        )


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


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def add_link(
    session: Session,
    user: User,
    *,
    telegram_chat_id: int | None,
    linked_at: datetime,
    unlinked_at: datetime | None = None,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        phone_verified_at=(
            linked_at if telegram_chat_id is not None and unlinked_at is None else None
        ),
        updated_at=unlinked_at or linked_at,
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
    consumed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS),
        consumed_at=consumed_at,
        invalidated_at=invalidated_at,
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
    phone = session.scalar(
        select(User.phone)
        .join(TelegramLinkToken, TelegramLinkToken.user_id == User.id)
        .where(TelegramLinkToken.token_hash == hash_telegram_link_token(raw))
    )
    assert phone is not None
    chat_identity = VerifiedPrivateTelegramChatIdentity(telegram_chat_id)
    sender_identity = TelegramUserIdentity(telegram_chat_id)
    bind_start_token_for_contact(
        session,
        raw,
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=_CONTACT_BINDING_KEY,
        now=now,
    )
    return consume_start_token(
        session,
        chat_identity,
        sender_identity,
        sender_identity,
        SensitiveTelegramContactPhone(phone),
        rate_limit_hmac_key=_CONTACT_BINDING_KEY,
        now=now,
    )


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def stored_unlink_domain_text(session: Session) -> str:
    queries = (
        (
            "telegram_links",
            "SELECT telegram_chat_id::text, linked_at::text, "
            "unlinked_at::text, updated_at::text FROM telegram_links",
        ),
        (
            "telegram_link_tokens",
            "SELECT token_hash, created_at::text, expires_at::text, "
            "consumed_at::text, invalidated_at::text FROM telegram_link_tokens",
        ),
        (
            "telegram_link_events",
            "SELECT action, occurred_at::text FROM telegram_link_events",
        ),
    )
    values: list[str] = []
    for table_name, query in queries:
        for row in session.execute(text(query)).all():
            values.append(table_name)
            values.extend(str(value) for value in row)
    return "|".join(values)


def assert_sensitive_values_absent(
    sensitive_values: tuple[str, ...],
    *texts: str,
) -> None:
    if any(value in text for value in sensitive_values for text in texts):
        pytest.fail("sensitive Telegram lifecycle value leaked", pytrace=False)


def test_unlink_public_api_has_no_password_chat_or_external_user_id() -> None:
    parameters = signature(unlink_telegram).parameters

    assert list(parameters) == ["session", "current_user", "now"]
    assert "user_id" not in parameters
    assert "chat_id" not in parameters
    assert "telegram_chat_id" not in parameters
    assert "raw_chat_id" not in parameters
    assert "password" not in parameters
    assert "current_password" not in parameters
    assert "raw_password" not in parameters

    source = getsource(unlink_telegram)
    assert "commit(" not in source
    assert "rollback(" not in source
    assert "password" not in source
    assert "chat_identity" not in source


@pytest.mark.integration
def test_unlink_active_link_tombstones_invalidates_token_and_writes_event(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "unlink_outstanding_token"
    consumed_raw_token = "unlink_consumed_token"
    invalidated_raw_token = "unlink_invalidated_token"
    old_chat_id = 12_345_600
    linked_at = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)
    now = linked_at + timedelta(minutes=7)
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = add_user(first_session, "+998900014001")
        link = add_link(
            first_session,
            user,
            telegram_chat_id=old_chat_id,
            linked_at=linked_at,
        )
        outstanding_token = add_token(
            first_session,
            user,
            raw_token=raw_token,
            created_at=linked_at + timedelta(minutes=1),
        )
        consumed_token = add_token(
            first_session,
            user,
            raw_token=consumed_raw_token,
            created_at=linked_at + timedelta(minutes=2),
            consumed_at=linked_at + timedelta(minutes=3),
        )
        invalidated_token = add_token(
            first_session,
            user,
            raw_token=invalidated_raw_token,
            created_at=linked_at + timedelta(minutes=4),
            invalidated_at=linked_at + timedelta(minutes=5),
        )
        first_session.commit()
        user_id = user.id
        link_id = link.id
        outstanding_token_id = outstanding_token.id
        consumed_token_id = consumed_token.id
        invalidated_token_id = invalidated_token.id

        user_in_transaction = first_session.get(User, user_id)
        assert user_in_transaction is not None
        session_spy = SessionSpy(first_session)

        with caplog.at_level(logging.INFO):
            result = unlink_telegram(session_spy, user_in_transaction, now)
        link_in_transaction = first_session.get(TelegramLink, link_id)
        token_in_transaction = first_session.get(
            TelegramLinkToken,
            outstanding_token_id,
        )
        event_in_transaction = first_session.scalar(select(TelegramLinkEvent))

        assert isinstance(result, UnlinkedTelegramLink)
        assert result.link is link_in_transaction
        assert result.event is event_in_transaction
        assert result.invalidated_token_count == 1
        assert result.link.telegram_chat_id is None
        assert result.link.linked_at == linked_at
        assert result.link.unlinked_at == now
        assert result.link.phone_verified_at is None
        assert result.link.updated_at == now
        assert result.event.user_id == user_id
        assert result.event.action == "unlinked"
        assert result.event.occurred_at == now
        assert token_in_transaction is not None
        assert token_in_transaction.invalidated_at == now
        assert first_session.get(TelegramLinkToken, consumed_token_id).consumed_at == (
            linked_at + timedelta(minutes=3)
        )
        assert first_session.get(
            TelegramLinkToken,
            invalidated_token_id,
        ).invalidated_at == linked_at + timedelta(minutes=5)
        assert count_table(first_session, TelegramLinkEvent) == 1
        assert count_table(first_session, Customer) == 0
        assert second_session.get(TelegramLink, link_id).telegram_chat_id == old_chat_id
        assert (
            second_session.get(TelegramLinkToken, outstanding_token_id).invalidated_at
            is None
        )
        assert count_table(second_session, TelegramLinkEvent) == 0
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert session_spy.close_called is False
        assert str(old_chat_id) not in stored_unlink_domain_text(first_session)
        assert str(old_chat_id) not in repr(result)
        assert str(old_chat_id) not in caplog.text
        assert raw_token not in stored_unlink_domain_text(first_session)
        assert raw_token not in repr(result)
        assert raw_token not in caplog.text

        first_session.commit()
    finally:
        first_session.close()
        second_session.close()

    verify_session = session_factory()
    try:
        stored_link = verify_session.get(TelegramLink, link_id)
        stored_token = verify_session.get(TelegramLinkToken, outstanding_token_id)
        stored_events = verify_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()

        assert stored_link is not None
        assert stored_link.telegram_chat_id is None
        assert stored_link.linked_at == linked_at
        assert stored_link.unlinked_at == now
        assert stored_link.phone_verified_at is None
        assert stored_link.updated_at == now
        assert stored_token is not None
        assert stored_token.invalidated_at == now
        assert len(stored_events) == 1
        assert stored_events[0].action == "unlinked"
        assert stored_events[0].occurred_at == now
        assert str(old_chat_id) not in stored_unlink_domain_text(verify_session)
    finally:
        verify_session.rollback()
        verify_session.close()


@pytest.mark.parametrize("has_tombstone", [False, True])
@pytest.mark.integration
def test_unlink_without_active_link_preserves_state_token_and_events(
    has_tombstone: bool,
    db_session: Session,
) -> None:
    raw_token = "unlink_rejected_outstanding_token"
    linked_at = datetime(2026, 7, 24, 21, 20, tzinfo=UTC)
    tombstone_unlinked_at = linked_at + timedelta(minutes=2)
    now = linked_at + timedelta(minutes=5)
    user = add_user(
        db_session,
        "+998900014002" if has_tombstone else "+998900014003",
    )
    link = None
    if has_tombstone:
        link = add_link(
            db_session,
            user,
            telegram_chat_id=None,
            linked_at=linked_at,
            unlinked_at=tombstone_unlinked_at,
        )
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=linked_at + timedelta(minutes=1),
    )
    existing_event = None
    if has_tombstone:
        existing_event = TelegramLinkEvent(
            user_id=user.id,
            action="unlinked",
            occurred_at=tombstone_unlinked_at,
        )
        db_session.add(existing_event)
        db_session.flush()

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        unlink_telegram(db_session, user, now)
    db_session.refresh(token)
    if link is not None:
        db_session.refresh(link)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
    continuation_user = add_user(
        db_session,
        "+998900014004" if has_tombstone else "+998900014005",
    )

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_NOT_LINKED
    assert exc_info.value.public_error == {
        "code": "TELEGRAM_NOT_LINKED",
        "message": "Telegram akkauntingiz bog'lanmagan.",
    }
    assert token.consumed_at is None
    assert token.invalidated_at is None
    if link is None:
        assert count_table(db_session, TelegramLink) == 0
    else:
        assert link.telegram_chat_id is None
        assert link.linked_at == linked_at
        assert link.unlinked_at == tombstone_unlinked_at
        assert link.updated_at == tombstone_unlinked_at
        assert existing_event is not None
        db_session.refresh(existing_event)
        assert existing_event.action == "unlinked"
        assert existing_event.occurred_at == tombstone_unlinked_at
    assert count_table(db_session, TelegramLinkEvent) == (1 if has_tombstone else 0)
    assert continuation_user.id is not None
    assert raw_token not in error_text
    assert user.phone not in error_text


@pytest.mark.integration
def test_parallel_double_unlink_has_exactly_one_transition_and_event(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "parallel_double_unlink_outstanding_token"
    old_chat_id = 12_345_700
    linked_at = datetime(2026, 7, 24, 21, 40, tzinfo=UTC)
    unlink_at_by_label = {
        "first": linked_at + timedelta(minutes=3),
        "second": linked_at + timedelta(minutes=3, seconds=1),
    }
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = add_user(setup_session, "+998900014101")
        link = add_link(
            setup_session,
            user,
            telegram_chat_id=old_chat_id,
            linked_at=linked_at,
        )
        outstanding_token = add_token(
            setup_session,
            user,
            raw_token=raw_token,
            created_at=linked_at + timedelta(minutes=1),
        )
        user_id = user.id
        link_id = link.id
        token_id = outstanding_token.id
        setup_session.commit()
    finally:
        setup_session.close()

    unlink_barrier = Barrier(2)
    original_has_active_link = telegram_service.has_active_telegram_link
    sensitive_values = (
        raw_token,
        str(old_chat_id),
        str(user_id),
        "+998900014101",
    )

    def synchronized_link_discovery(
        session: Session,
        current_user: User,
    ) -> bool:
        result = original_has_active_link(session, current_user)
        unlink_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return result

    def worker(label: str) -> ParallelUnlinkOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            if current_user is None:
                return ParallelUnlinkOutcome(
                    label=label,
                    kind="unexpected",
                    exception_class="MissingUser",
                )
            try:
                result = unlink_telegram(
                    session,
                    current_user,
                    unlink_at_by_label[label],
                )
            except TelegramLinkTokenIssueError as exc:
                error_text = f"{exc!r} {exc} {exc.public_error}"
                for value in sensitive_values:
                    assert value not in error_text
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return ParallelUnlinkOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=exc.error_code,
                    session_usable=session_usable,
                )

            session_usable = session.scalar(select(1)) == 1
            result_unlinked_at = result.link.unlinked_at
            invalidated_token_count = result.invalidated_token_count
            session.commit()
            return ParallelUnlinkOutcome(
                label=label,
                kind="unlinked",
                invalidated_token_count=invalidated_token_count,
                unlinked_at=result_unlinked_at,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelUnlinkOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelUnlinkOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    monkeypatch.setattr(
        telegram_service,
        "has_active_telegram_link",
        synchronized_link_discovery,
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(worker, label) for label in ("first", "second")]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            unlink_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel Telegram unlink timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    winners = [outcome for outcome in outcomes if outcome.kind == "unlinked"]
    losers = [outcome for outcome in outcomes if outcome.kind == "domain_error"]
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    final_session = session_factory()
    try:
        stored_link = final_session.get(TelegramLink, link_id)
        stored_token = final_session.get(TelegramLinkToken, token_id)
        stored_events = final_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()
        link_count = count_table(final_session, TelegramLink)
        token_count = count_table(final_session, TelegramLinkToken)
        stored_text = stored_unlink_domain_text(final_session)
        final_session_usable = final_session.scalar(select(1)) == 1
    finally:
        final_session.close()

    assert unexpected == []
    assert len(winners) == 1
    assert winners[0].invalidated_token_count == 1
    assert winners[0].unlinked_at == unlink_at_by_label[winners[0].label]
    assert len(losers) == 1
    assert losers[0].error_code is ErrorCode.TELEGRAM_NOT_LINKED
    assert all(outcome.session_usable for outcome in outcomes)
    assert final_session_usable is True
    assert stored_link is not None
    assert stored_link.telegram_chat_id is None
    assert stored_link.linked_at == linked_at
    assert stored_link.unlinked_at == unlink_at_by_label[winners[0].label]
    assert stored_link.phone_verified_at is None
    assert stored_link.updated_at == unlink_at_by_label[winners[0].label]
    assert stored_token is not None
    assert stored_token.consumed_at is None
    assert stored_token.invalidated_at == unlink_at_by_label[winners[0].label]
    assert len(stored_events) == 1
    assert stored_events[0].action == "unlinked"
    assert stored_events[0].occurred_at == unlink_at_by_label[winners[0].label]
    assert link_count == 1
    assert token_count == 1
    assert "IntegrityError" not in outcome_text
    assert "IntegrityError" not in captured_log
    assert "telegram_links" not in outcome_text
    for value in sensitive_values:
        assert value not in outcome_text
        assert value not in captured_log
        assert value not in stored_text


@pytest.mark.integration
def test_unlink_then_parallel_relink_consume_rejects_invalidated_token(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "unlink_first_relink_race_token"
    chat_a = 12_345_800
    chat_b = 12_345_801
    linked_at = datetime(2026, 7, 24, 22, 0, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=1)
    unlink_at = linked_at + timedelta(minutes=3)
    consume_at = unlink_at + timedelta(seconds=1)
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = add_user(setup_session, "+998900014201")
        link = add_link(
            setup_session,
            user,
            telegram_chat_id=chat_a,
            linked_at=linked_at,
        )
        token = add_token(
            setup_session,
            user,
            raw_token=raw_token,
            created_at=issued_at,
        )
        user_id = user.id
        link_id = link.id
        token_id = token.id
        setup_session.commit()
    finally:
        setup_session.close()

    start_barrier = Barrier(2)
    unlink_committed = Event()
    sensitive_values = (
        raw_token,
        str(chat_a),
        str(chat_b),
        str(user_id),
        "+998900014201",
    )

    def unlink_worker() -> ParallelLifecycleRaceOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            if current_user is None:
                return ParallelLifecycleRaceOutcome(
                    label="unlink",
                    kind="unexpected",
                    exception_class="MissingUser",
                )
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            result = unlink_telegram(session, current_user, unlink_at)
            result_unlinked_at = result.link.unlinked_at
            invalidated_token_count = result.invalidated_token_count
            event_action = result.event.action
            event_at = result.event.occurred_at
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            unlink_committed.set()
            return ParallelLifecycleRaceOutcome(
                label="unlink",
                kind="unlinked",
                invalidated_token_count=invalidated_token_count,
                unlinked_at=result_unlinked_at,
                event_action=event_action,
                event_at=event_at,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="unlink",
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="unlink",
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            unlink_committed.set()
            session.close()

    def consume_worker() -> ParallelLifecycleRaceOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            if not unlink_committed.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
                return ParallelLifecycleRaceOutcome(
                    label="consume",
                    kind="unexpected",
                    exception_class="UnlinkTimeout",
                )
            try:
                consume_raw(
                    session,
                    raw_token=raw_token,
                    telegram_chat_id=chat_b,
                    now=consume_at,
                )
            except TelegramLinkTokenConsumeError as exc:
                error_text = f"{exc!r} {exc} {exc.public_error}"
                assert_sensitive_values_absent(sensitive_values, error_text)
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return ParallelLifecycleRaceOutcome(
                    label="consume",
                    kind="consume_error",
                    error_code=exc.error_code,
                    session_usable=session_usable,
                )
            session.commit()
            return ParallelLifecycleRaceOutcome(
                label="consume",
                kind="unexpected",
                exception_class="ConsumeSucceeded",
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="consume",
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="consume",
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(unlink_worker), executor.submit(consume_worker)]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            unlink_committed.set()
            for future in not_done:
                future.cancel()
            pytest.fail("unlink-first relink race timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    unlink_outcomes = [outcome for outcome in outcomes if outcome.kind == "unlinked"]
    consume_errors = [
        outcome for outcome in outcomes if outcome.kind == "consume_error"
    ]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    verify_session = session_factory()
    try:
        replay_error_text = ""
        with pytest.raises(TelegramLinkTokenConsumeError) as replay_exc_info:
            consume_raw(
                verify_session,
                raw_token=raw_token,
                telegram_chat_id=chat_b,
                now=consume_at + timedelta(seconds=1),
            )
        replay_error_text = (
            f"{replay_exc_info.value!r} {replay_exc_info.value} "
            f"{replay_exc_info.value.public_error}"
        )
        stored_link = verify_session.get(TelegramLink, link_id)
        stored_token = verify_session.get(TelegramLinkToken, token_id)
        stored_events = verify_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()
        stored_link_state = (
            None
            if stored_link is None
            else (
                stored_link.telegram_chat_id,
                stored_link.linked_at,
                stored_link.unlinked_at,
                stored_link.updated_at,
            )
        )
        stored_token_state = (
            None
            if stored_token is None
            else (
                stored_token.consumed_at,
                stored_token.invalidated_at,
            )
        )
        stored_event_snapshots = [
            (event.action, event.occurred_at) for event in stored_events
        ]
        active_link_count = (
            verify_session.scalar(
                select(func.count())
                .select_from(TelegramLink)
                .where(
                    TelegramLink.telegram_chat_id.is_not(None),
                    TelegramLink.unlinked_at.is_(None),
                )
            )
            or 0
        )
        stored_text = stored_unlink_domain_text(verify_session)
        verify_session_usable = verify_session.scalar(select(1)) == 1
    finally:
        verify_session.rollback()
        verify_session.close()

    assert unexpected == []
    assert len(unlink_outcomes) == 1
    assert unlink_outcomes[0].invalidated_token_count == 1
    assert unlink_outcomes[0].unlinked_at == unlink_at
    assert unlink_outcomes[0].event_action == "unlinked"
    assert unlink_outcomes[0].event_at == unlink_at
    assert len(consume_errors) == 1
    assert consume_errors[0].error_code is ErrorCode.LINK_TOKEN_INVALID
    assert all(outcome.session_usable for outcome in outcomes)
    assert stored_link_state == (None, linked_at, unlink_at, unlink_at)
    assert stored_token_state == (None, unlink_at)
    assert active_link_count == 0
    assert stored_event_snapshots == [("unlinked", unlink_at)]
    assert verify_session_usable is True
    assert "IntegrityError" not in outcome_text
    assert "IntegrityError" not in captured_log
    assert_sensitive_values_absent(
        sensitive_values,
        outcome_text,
        captured_log,
        stored_text,
        replay_error_text,
    )


@pytest.mark.integration
def test_relink_consume_then_parallel_unlink_finalizes_unlinked_state(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "relink_first_unlink_race_token"
    chat_a = 12_345_802
    chat_b = 12_345_803
    linked_at = datetime(2026, 7, 24, 22, 10, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=1)
    relink_at = linked_at + timedelta(minutes=3)
    unlink_at = relink_at + timedelta(seconds=1)
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = add_user(setup_session, "+998900014202")
        link = add_link(
            setup_session,
            user,
            telegram_chat_id=chat_a,
            linked_at=linked_at,
        )
        token = add_token(
            setup_session,
            user,
            raw_token=raw_token,
            created_at=issued_at,
        )
        user_id = user.id
        link_id = link.id
        token_id = token.id
        setup_session.commit()
    finally:
        setup_session.close()

    start_barrier = Barrier(2)
    relink_committed = Event()
    sensitive_values = (
        raw_token,
        str(chat_a),
        str(chat_b),
        str(user_id),
        "+998900014202",
    )

    def consume_worker() -> ParallelLifecycleRaceOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            result = consume_raw(
                session,
                raw_token=raw_token,
                telegram_chat_id=chat_b,
                now=relink_at,
            )
            event_action = result.event.action if result.event is not None else None
            event_at = result.event.occurred_at if result.event is not None else None
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            relink_committed.set()
            return ParallelLifecycleRaceOutcome(
                label="consume",
                kind="consumed",
                consume_outcome=result.outcome,
                event_action=event_action,
                event_at=event_at,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="consume",
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="consume",
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            relink_committed.set()
            session.close()

    def unlink_worker() -> ParallelLifecycleRaceOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            if current_user is None:
                return ParallelLifecycleRaceOutcome(
                    label="unlink",
                    kind="unexpected",
                    exception_class="MissingUser",
                )
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            if not relink_committed.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
                return ParallelLifecycleRaceOutcome(
                    label="unlink",
                    kind="unexpected",
                    exception_class="RelinkTimeout",
                )
            result = unlink_telegram(session, current_user, unlink_at)
            result_unlinked_at = result.link.unlinked_at
            invalidated_token_count = result.invalidated_token_count
            event_action = result.event.action
            event_at = result.event.occurred_at
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return ParallelLifecycleRaceOutcome(
                label="unlink",
                kind="unlinked",
                invalidated_token_count=invalidated_token_count,
                unlinked_at=result_unlinked_at,
                event_action=event_action,
                event_at=event_at,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="unlink",
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelLifecycleRaceOutcome(
                label="unlink",
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(consume_worker), executor.submit(unlink_worker)]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            relink_committed.set()
            for future in not_done:
                future.cancel()
            pytest.fail("relink-first unlink race timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    consume_outcomes = [outcome for outcome in outcomes if outcome.kind == "consumed"]
    unlink_outcomes = [outcome for outcome in outcomes if outcome.kind == "unlinked"]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    verify_session = session_factory()
    try:
        replay_error_text = ""
        with pytest.raises(TelegramLinkTokenConsumeError) as replay_exc_info:
            consume_raw(
                verify_session,
                raw_token=raw_token,
                telegram_chat_id=chat_b,
                now=unlink_at + timedelta(seconds=1),
            )
        replay_error_text = (
            f"{replay_exc_info.value!r} {replay_exc_info.value} "
            f"{replay_exc_info.value.public_error}"
        )
        stored_link = verify_session.get(TelegramLink, link_id)
        stored_token = verify_session.get(TelegramLinkToken, token_id)
        stored_events = verify_session.scalars(
            select(TelegramLinkEvent)
            .where(TelegramLinkEvent.user_id == user_id)
            .order_by(TelegramLinkEvent.occurred_at)
        ).all()
        stored_link_state = (
            None
            if stored_link is None
            else (
                stored_link.telegram_chat_id,
                stored_link.linked_at,
                stored_link.unlinked_at,
                stored_link.updated_at,
            )
        )
        stored_token_state = (
            None
            if stored_token is None
            else (
                stored_token.consumed_at,
                stored_token.invalidated_at,
            )
        )
        stored_event_snapshots = [
            (event.action, event.occurred_at) for event in stored_events
        ]
        active_link_count = (
            verify_session.scalar(
                select(func.count())
                .select_from(TelegramLink)
                .where(
                    TelegramLink.telegram_chat_id.is_not(None),
                    TelegramLink.unlinked_at.is_(None),
                )
            )
            or 0
        )
        stored_text = stored_unlink_domain_text(verify_session)
        verify_session_usable = verify_session.scalar(select(1)) == 1
    finally:
        verify_session.rollback()
        verify_session.close()

    assert unexpected == []
    assert len(consume_outcomes) == 1
    assert (
        consume_outcomes[0].consume_outcome is TelegramStartTokenConsumeOutcome.RELINKED
    )
    assert consume_outcomes[0].event_action == "relinked"
    assert consume_outcomes[0].event_at == relink_at
    assert len(unlink_outcomes) == 1
    assert unlink_outcomes[0].invalidated_token_count == 0
    assert unlink_outcomes[0].unlinked_at == unlink_at
    assert unlink_outcomes[0].event_action == "unlinked"
    assert unlink_outcomes[0].event_at == unlink_at
    assert all(outcome.session_usable for outcome in outcomes)
    assert stored_link_state == (None, relink_at, unlink_at, unlink_at)
    assert stored_token_state == (relink_at, None)
    assert active_link_count == 0
    assert stored_event_snapshots == [
        ("relinked", relink_at),
        ("unlinked", unlink_at),
    ]
    assert verify_session_usable is True
    assert "IntegrityError" not in outcome_text
    assert "IntegrityError" not in captured_log
    assert_sensitive_values_absent(
        sensitive_values,
        outcome_text,
        captured_log,
        stored_text,
        replay_error_text,
    )


@pytest.mark.integration
@pytest.mark.parametrize("winner", ("issue", "unlink"))
def test_relink_token_issue_and_unlink_empty_set_barriers_converge(
    monkeypatch,
    m2_test_database: Engine,
    winner: str,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 17, 1 if winner == "issue" else 2, tzinfo=UTC)
    settings = Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=m2_test_database.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        telegram_link_rate_limit_user_attempts=20,
        telegram_link_rate_limit_phone_attempts=20,
        telegram_link_rate_limit_ip_attempts=50,
        rate_limit_hmac_key=_R05_RATE_LIMIT_HMAC_KEY,
    )
    setup_session = session_factory()
    try:
        user = add_user(
            setup_session,
            "+998900014301" if winner == "issue" else "+998900014302",
        )
        link = add_link(
            setup_session,
            user,
            telegram_chat_id=12_345_901 if winner == "issue" else 12_345_902,
            linked_at=now,
        )
        user_id = user.id
        link_id = link.id
        setup_session.commit()
    finally:
        setup_session.close()

    issue_token_insert_entered = Event()
    issue_token_inserted = Event()
    unlink_empty_snapshot = Event()
    issue_committed = Event()
    original_insert = telegram_service.invalidate_and_insert_telegram_link_token
    original_token_set_lock = (
        telegram_service.lock_outstanding_telegram_link_token_set_by_user
    )
    original_user_lock = telegram_service._lock_active_user

    def wait_for(event: Event) -> None:
        if not event.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
            raise BrokenBarrierError

    def synchronized_insert(*args, **kwargs):
        session = args[0]
        if winner == "unlink" and session.info.get("race_actor") == "issue":
            wait_for(unlink_empty_snapshot)
            issue_token_insert_entered.set()
        token = original_insert(*args, **kwargs)
        if session.info.get("race_actor") == "issue":
            issue_token_inserted.set()
            if winner == "issue":
                wait_for(unlink_empty_snapshot)
        return token

    def synchronized_token_set_lock(*args, **kwargs):
        session = args[0]
        if winner == "issue" and session.info.get("race_actor") == "unlink":
            wait_for(issue_token_inserted)
        locked_tokens = original_token_set_lock(*args, **kwargs)
        if session.info.get("race_actor") == "unlink":
            assert locked_tokens == ()
            unlink_empty_snapshot.set()
        return locked_tokens

    def synchronized_user_lock(*args, **kwargs):
        session = args[0]
        actor = session.info.get("race_actor")
        if winner == "issue" and actor == "unlink":
            wait_for(issue_committed)
            return original_user_lock(*args, **kwargs)
        locked_user = original_user_lock(*args, **kwargs)
        if winner == "unlink" and actor == "unlink":
            wait_for(issue_token_insert_entered)
        return locked_user

    monkeypatch.setattr(
        telegram_service,
        "invalidate_and_insert_telegram_link_token",
        synchronized_insert,
    )
    monkeypatch.setattr(
        telegram_service,
        "lock_outstanding_telegram_link_token_set_by_user",
        synchronized_token_set_lock,
    )
    monkeypatch.setattr(
        telegram_service,
        "_lock_active_user",
        synchronized_user_lock,
    )

    def issue_worker() -> tuple[str, str]:
        session = session_factory()
        session.info["race_actor"] = "issue"
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            assert current_user is not None
            try:
                issue_relink_token(
                    session,
                    settings,
                    current_user,
                    ResolvedClientIp(
                        "203.0.113.231" if winner == "issue" else "203.0.113.232"
                    ),
                    now + timedelta(seconds=1),
                    token_generator=lambda _byte_count: (
                        "r05_issue_wins_empty_set"
                        if winner == "issue"
                        else "r05_unlink_wins_empty_set"
                    ),
                )
                outcome = "ISSUED"
            except TelegramLinkTokenIssueError as exc:
                outcome = exc.error_code.value
            session.commit()
            return "issue", outcome
        except BrokenBarrierError:
            session.rollback()
            return "issue", "BROKEN_BARRIER"
        except Exception as exc:
            session.rollback()
            return "issue", type(exc).__name__
        finally:
            issue_committed.set()
            session.close()

    def unlink_worker() -> tuple[str, str]:
        session = session_factory()
        session.info["race_actor"] = "unlink"
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            assert current_user is not None
            try:
                unlink_telegram(session, current_user, now + timedelta(seconds=2))
                outcome = "UNLINKED"
            except TelegramLinkTokenIssueError as exc:
                outcome = exc.error_code.value
            session.commit()
            return "unlink", outcome
        except BrokenBarrierError:
            session.rollback()
            return "unlink", "BROKEN_BARRIER"
        except Exception as exc:
            session.rollback()
            return "unlink", type(exc).__name__
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(issue_worker), executor.submit(unlink_worker)]
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            issue_token_insert_entered.set()
            issue_token_inserted.set()
            unlink_empty_snapshot.set()
            issue_committed.set()
            for future in not_done:
                future.cancel()
            pytest.fail("empty-set issue/unlink barrier timed out", pytrace=False)
        outcomes = dict(future.result(timeout=0) for future in futures)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    expected_outcomes = {
        "issue": {
            "issue": "ISSUED",
            "unlink": ErrorCode.RATE_LIMITED.value,
        },
        "unlink": {
            "issue": ErrorCode.TELEGRAM_NOT_LINKED.value,
            "unlink": "UNLINKED",
        },
    }
    assert outcomes == expected_outcomes[winner]

    verify_session = session_factory()
    try:
        stored_link = verify_session.get(TelegramLink, link_id)
        stored_tokens = tuple(
            verify_session.scalars(
                select(TelegramLinkToken).where(TelegramLinkToken.user_id == user_id)
            ).all()
        )
        event_actions = tuple(
            verify_session.scalars(
                select(TelegramLinkEvent.action).where(
                    TelegramLinkEvent.user_id == user_id
                )
            ).all()
        )
    finally:
        verify_session.close()

    assert stored_link is not None
    if winner == "issue":
        assert stored_link.telegram_chat_id is not None
        assert stored_link.unlinked_at is None
        assert len(stored_tokens) == 1
        assert stored_tokens[0].consumed_at is None
        assert stored_tokens[0].invalidated_at is None
        assert event_actions == ()
    else:
        assert stored_link.telegram_chat_id is None
        assert stored_link.unlinked_at is not None
        assert stored_tokens == ()
        assert event_actions == ("unlinked",)
