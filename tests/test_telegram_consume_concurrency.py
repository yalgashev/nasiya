import logging
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier, BrokenBarrierError

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.service as telegram_service
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkOutcome,
    TelegramLinkTokenConsumeError,
    consume_start_token,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token

_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, repr=False)
class ParallelConsumeOutcome:
    label: str
    kind: str
    outcome: TelegramLinkOutcome | None = None
    error_code: ErrorCode | None = None
    consumed_at: datetime | None = None
    session_usable: bool = False
    exception_class: str | None = None

    def __repr__(self) -> str:
        return (
            "ParallelConsumeOutcome("
            f"label={self.label!r}, kind={self.kind!r}, "
            f"outcome={self.outcome}, error_code={self.error_code}, "
            f"session_usable={self.session_usable}, "
            f"exception_class={self.exception_class!r}"
            ")"
        )


def add_user_and_token(
    session: Session,
    *,
    phone: str,
    raw_token: RawTelegramLinkToken,
    created_at: datetime,
) -> tuple[User, TelegramLinkToken]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(raw_token),
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=10),
    )
    session.add(token)
    session.flush()
    return user, token


def assert_sensitive_values_absent(
    sensitive_values: tuple[str, ...],
    *texts: str,
) -> None:
    if any(value in text for value in sensitive_values for text in texts):
        pytest.fail("sensitive Telegram consume value leaked", pytrace=False)


@pytest.mark.integration
def test_same_token_two_chat_parallel_consume_has_exactly_one_winner(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    raw_value = "parallel_same_token_two_chat_capability"
    raw_token = RawTelegramLinkToken(raw_value)
    issued_at = datetime(2026, 7, 25, 11, 30, tzinfo=UTC)
    setup_session = session_factory()
    try:
        user, token = add_user_and_token(
            setup_session,
            phone="+998900017101",
            raw_token=raw_token,
            created_at=issued_at,
        )
        user_id = user.id
        token_id = token.id
        setup_session.commit()
    finally:
        setup_session.close()

    chat_by_label = {
        "first": VerifiedPrivateTelegramChatIdentity(17_101),
        "second": VerifiedPrivateTelegramChatIdentity(17_102),
    }
    consume_at_by_label = {
        "first": issued_at + timedelta(minutes=1),
        "second": issued_at + timedelta(minutes=1, seconds=1),
    }
    token_lock_barrier = Barrier(2)
    original_locked_lookup = (
        telegram_service.get_valid_telegram_link_token_for_consume_by_hash_for_update
    )

    def synchronized_locked_lookup(
        session: Session,
        token_hash: str,
        now: datetime,
    ) -> TelegramLinkToken | None:
        token_lock_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return original_locked_lookup(session, token_hash, now)

    def worker(label: str) -> ParallelConsumeOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                result = consume_start_token(
                    session,
                    RawTelegramLinkToken(raw_value),
                    chat_by_label[label],
                    consume_at_by_label[label],
                )
            except TelegramLinkTokenConsumeError as exc:
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return ParallelConsumeOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=exc.error_code,
                    session_usable=session_usable,
                )

            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return ParallelConsumeOutcome(
                label=label,
                kind="consumed",
                outcome=result.outcome,
                consumed_at=result.token.consumed_at,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelConsumeOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelConsumeOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    monkeypatch.setattr(
        telegram_service,
        "get_valid_telegram_link_token_for_consume_by_hash_for_update",
        synchronized_locked_lookup,
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [
                executor.submit(worker, label) for label in ("first", "second")
            ]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            token_lock_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel Telegram token consume timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    winners = [outcome for outcome in outcomes if outcome.kind == "consumed"]
    losers = [outcome for outcome in outcomes if outcome.kind == "domain_error"]
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    final_session = session_factory()
    try:
        stored_token = final_session.get(TelegramLinkToken, token_id)
        links = final_session.scalars(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        ).all()
        active_link_count = (
            final_session.scalar(
                select(func.count())
                .select_from(TelegramLink)
                .where(
                    TelegramLink.user_id == user_id,
                    TelegramLink.telegram_chat_id.is_not(None),
                    TelegramLink.unlinked_at.is_(None),
                )
            )
            or 0
        )
        tombstone_count = (
            final_session.scalar(
                select(func.count())
                .select_from(TelegramLink)
                .where(
                    TelegramLink.user_id == user_id,
                    TelegramLink.telegram_chat_id.is_(None),
                    TelegramLink.unlinked_at.is_not(None),
                )
            )
            or 0
        )
        events = final_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()
        final_session_usable = final_session.scalar(select(1)) == 1
    finally:
        final_session.close()

    assert unexpected == []
    assert len(winners) == 1
    assert winners[0].outcome is TelegramLinkOutcome.LINKED
    assert len(losers) == 1
    assert losers[0].error_code is ErrorCode.LINK_TOKEN_INVALID
    assert all(outcome.session_usable for outcome in outcomes)
    assert final_session_usable is True
    assert stored_token is not None
    assert stored_token.consumed_at == winners[0].consumed_at
    assert stored_token.consumed_at == consume_at_by_label[winners[0].label]
    assert stored_token.invalidated_at is None
    assert len(links) == 1
    assert active_link_count == 1
    assert tombstone_count == 0
    assert links[0].unlinked_at is None
    if (
        links[0].telegram_chat_id
        != chat_by_label[winners[0].label].as_bigint()
    ):
        pytest.fail("active Telegram link does not belong to the winner", pytrace=False)
    assert len(events) == 1
    assert events[0].action in {"linked", "relinked"}
    assert events[0].action == "linked"
    assert events[0].occurred_at == winners[0].consumed_at
    assert_sensitive_values_absent(
        (
            raw_value,
            *(str(identity.as_bigint()) for identity in chat_by_label.values()),
        ),
        outcome_text,
        captured_log,
    )


@pytest.mark.integration
def test_same_chat_two_user_parallel_consume_keeps_loser_token_reusable(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    raw_value_by_label = {
        "user_a": "parallel_same_chat_user_a_capability",
        "user_b": "parallel_same_chat_user_b_capability",
    }
    phone_by_label = {
        "user_a": "+998900017201",
        "user_b": "+998900017202",
    }
    issued_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    setup_session = session_factory()
    try:
        user_id_by_label = {}
        token_id_by_label = {}
        for label in ("user_a", "user_b"):
            user, token = add_user_and_token(
                setup_session,
                phone=phone_by_label[label],
                raw_token=RawTelegramLinkToken(raw_value_by_label[label]),
                created_at=issued_at,
            )
            user_id_by_label[label] = user.id
            token_id_by_label[label] = token.id
        setup_session.commit()
    finally:
        setup_session.close()

    shared_identity = VerifiedPrivateTelegramChatIdentity(17_201)
    retry_identity = VerifiedPrivateTelegramChatIdentity(17_202)
    consume_at_by_label = {
        "user_a": issued_at + timedelta(minutes=1),
        "user_b": issued_at + timedelta(minutes=1, seconds=1),
    }
    link_mutation_barrier = Barrier(2)
    original_link_mutation = telegram_service.link_verified_private_chat

    sensitive_values = (
        *raw_value_by_label.values(),
        *phone_by_label.values(),
        str(shared_identity.as_bigint()),
        str(retry_identity.as_bigint()),
    )

    def synchronized_link_mutation(
        session: Session,
        current_user: User,
        chat_identity: VerifiedPrivateTelegramChatIdentity,
        now: datetime,
    ) -> TelegramLink | None:
        if chat_identity.as_bigint() == shared_identity.as_bigint():
            link_mutation_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return original_link_mutation(session, current_user, chat_identity, now)

    def worker(label: str) -> ParallelConsumeOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                result = consume_start_token(
                    session,
                    RawTelegramLinkToken(raw_value_by_label[label]),
                    shared_identity,
                    consume_at_by_label[label],
                )
            except TelegramChatAlreadyLinkedError as exc:
                error_text = f"{exc!r} {exc} {exc.public_error}"
                assert_sensitive_values_absent(sensitive_values, error_text)
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return ParallelConsumeOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=exc.error_code,
                    session_usable=session_usable,
                )

            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return ParallelConsumeOutcome(
                label=label,
                kind="consumed",
                outcome=result.outcome,
                consumed_at=result.token.consumed_at,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelConsumeOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelConsumeOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    monkeypatch.setattr(
        telegram_service,
        "link_verified_private_chat",
        synchronized_link_mutation,
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [
                executor.submit(worker, label) for label in ("user_a", "user_b")
            ]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            link_mutation_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel Telegram chat consume timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    winners = [outcome for outcome in outcomes if outcome.kind == "consumed"]
    losers = [outcome for outcome in outcomes if outcome.kind == "domain_error"]
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    final_session = session_factory()
    try:
        race_tokens = {
            label: final_session.get(TelegramLinkToken, token_id)
            for label, token_id in token_id_by_label.items()
        }
        race_links = final_session.scalars(
            select(TelegramLink).where(TelegramLink.unlinked_at.is_(None))
        ).all()
        race_events = final_session.scalars(
            select(TelegramLinkEvent).order_by(
                TelegramLinkEvent.user_id,
                TelegramLinkEvent.occurred_at,
            )
        ).all()
        race_event_count_by_label = {
            label: final_session.scalar(
                select(func.count())
                .select_from(TelegramLinkEvent)
                .where(TelegramLinkEvent.user_id == user_id)
            )
            or 0
            for label, user_id in user_id_by_label.items()
        }
        final_session_usable = final_session.scalar(select(1)) == 1
    finally:
        final_session.close()

    assert unexpected == []
    assert len(winners) == 1
    assert winners[0].outcome is TelegramLinkOutcome.LINKED
    assert len(losers) == 1
    assert losers[0].error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert all(outcome.session_usable for outcome in outcomes)
    assert final_session_usable is True
    assert len(race_links) == 1
    if race_links[0].telegram_chat_id != shared_identity.as_bigint():
        pytest.fail("shared Telegram chat was not linked", pytrace=False)
    if race_links[0].user_id != user_id_by_label[winners[0].label]:
        pytest.fail("shared Telegram chat owner does not match winner", pytrace=False)
    assert len(race_events) == 1
    assert race_events[0].action == "linked"
    if race_events[0].user_id != user_id_by_label[winners[0].label]:
        pytest.fail("winner lifecycle event belongs to a different user", pytrace=False)
    assert race_events[0].occurred_at == consume_at_by_label[winners[0].label]
    assert race_event_count_by_label[winners[0].label] == 1
    assert race_event_count_by_label[losers[0].label] == 0

    winner_token = race_tokens[winners[0].label]
    loser_token = race_tokens[losers[0].label]
    assert winner_token is not None
    assert winner_token.consumed_at == consume_at_by_label[winners[0].label]
    assert winner_token.invalidated_at is None
    assert loser_token is not None
    assert loser_token.consumed_at is None
    assert loser_token.invalidated_at is None

    retry_session = session_factory()
    retry_at = issued_at + timedelta(minutes=2)
    try:
        retry_result = consume_start_token(
            retry_session,
            RawTelegramLinkToken(raw_value_by_label[losers[0].label]),
            retry_identity,
            retry_at,
        )
        retry_outcome = retry_result.outcome
        retry_token_id = retry_result.token.id
        retry_token_consumed_at = retry_result.token.consumed_at
        retry_link_user_id = retry_result.link.user_id
        retry_link_chat_id = retry_result.link.telegram_chat_id
        retry_session_usable = retry_session.scalar(select(1)) == 1
        retry_session.commit()
    finally:
        retry_session.close()

    verify_session = session_factory()
    try:
        retry_loser_token = verify_session.get(
            TelegramLinkToken,
            token_id_by_label[losers[0].label],
        )
        event_count_by_label_after_retry = {
            label: verify_session.scalar(
                select(func.count())
                .select_from(TelegramLinkEvent)
                .where(TelegramLinkEvent.user_id == user_id)
            )
            or 0
            for label, user_id in user_id_by_label.items()
        }
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
        verify_session_usable = verify_session.scalar(select(1)) == 1
    finally:
        verify_session.close()

    assert retry_outcome is TelegramLinkOutcome.LINKED
    if retry_token_id != token_id_by_label[losers[0].label]:
        pytest.fail("loser token was not reused for retry", pytrace=False)
    if retry_link_user_id != user_id_by_label[losers[0].label]:
        pytest.fail("retry link belongs to a different user", pytrace=False)
    if retry_link_chat_id != retry_identity.as_bigint():
        pytest.fail("retry Telegram chat was not linked", pytrace=False)
    assert retry_token_consumed_at == retry_at
    assert retry_loser_token is not None
    assert retry_loser_token.consumed_at == retry_at
    assert retry_loser_token.invalidated_at is None
    assert event_count_by_label_after_retry[winners[0].label] == 1
    assert event_count_by_label_after_retry[losers[0].label] == 1
    assert active_link_count == 2
    assert retry_session_usable is True
    assert verify_session_usable is True
    assert_sensitive_values_absent(
        sensitive_values,
        outcome_text,
        captured_log,
    )
