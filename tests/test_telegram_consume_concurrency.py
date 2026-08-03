import logging
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier, BrokenBarrierError, Event

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer.models  # noqa: F401
import app.customer_document.models  # noqa: F401
import app.offers.models  # noqa: F401
import app.otp.dispatch_service as otp_dispatch_service
import app.telegram.repository as telegram_repository
import app.telegram.service as telegram_service
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.otp.contracts import (
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPurpose,
)
from app.otp.dispatch_service import prepare_next_otp_dispatch
from app.otp.issuance import issue_login_otp_in_transaction
from app.otp.models import OtpChallenge, OtpDispatch
from app.settings import Settings
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkOutcome,
    TelegramLinkTokenConsumeError,
    TelegramLinkTokenIssueError,
    bind_start_token_for_contact,
    consume_start_token,
    issue_relink_token_after_rate_limit,
    unlink,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token

_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 15
_CONTACT_BINDING_KEY = SecretStr(
    "test-contact-binding-concurrency-key-at-least-32-characters"
)


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


def add_verified_link(
    session: Session,
    *,
    user: User,
    chat_id: int,
    linked_at: datetime,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=chat_id,
        linked_at=linked_at,
        phone_verified_at=linked_at,
        updated_at=linked_at,
    )
    session.add(link)
    session.flush()
    return link


def add_active_customer(
    session: Session,
    *,
    user: User,
    activated_at: datetime,
) -> Customer:
    customer = Customer(
        user_id=user.id,
        onboarding_status="active",
        created_at=activated_at,
        activated_at=activated_at,
        updated_at=activated_at,
    )
    session.add(customer)
    session.flush()
    return customer


def bind_contact(
    session: Session,
    *,
    raw_token: str,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
    now: datetime,
) -> None:
    bind_start_token_for_contact(
        session,
        RawTelegramLinkToken(raw_token),
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=_CONTACT_BINDING_KEY,
        now=now,
    )


def consume_contact(
    session: Session,
    *,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
    contact_phone: str,
    now: datetime,
):
    return consume_start_token(
        session,
        chat_identity,
        sender_identity,
        sender_identity,
        SensitiveTelegramContactPhone(contact_phone),
        rate_limit_hmac_key=_CONTACT_BINDING_KEY,
        now=now,
    )


def assert_sensitive_values_absent(
    sensitive_values: tuple[str, ...],
    *texts: str,
) -> None:
    if any(value in text for value in sensitive_values for text in texts):
        pytest.fail("sensitive Telegram contact value leaked", pytrace=False)


@pytest.mark.integration
def test_same_bound_contact_parallel_verify_has_exactly_one_winner(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    raw_value = "parallel_same_bound_contact_capability"
    issued_at = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
    chat_identity = VerifiedPrivateTelegramChatIdentity(17_101)
    sender_identity = TelegramUserIdentity(17_101)
    phone = "+998900017101"
    with session_factory.begin() as session:
        user, token = add_user_and_token(
            session,
            phone=phone,
            raw_token=RawTelegramLinkToken(raw_value),
            created_at=issued_at,
        )
        user_id = user.id
        token_id = token.id
        bind_contact(
            session,
            raw_token=raw_value,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            now=issued_at + timedelta(seconds=1),
        )

    verify_at_by_label = {
        "first": issued_at + timedelta(seconds=2),
        "second": issued_at + timedelta(seconds=3),
    }
    token_lock_barrier = Barrier(2)
    original_token_lock = telegram_service.lock_telegram_link_token_set_by_ids

    def synchronized_token_lock(*args, **kwargs):
        token_lock_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return original_token_lock(*args, **kwargs)

    monkeypatch.setattr(
        telegram_service,
        "lock_telegram_link_token_set_by_ids",
        synchronized_token_lock,
    )

    def worker(label: str) -> ParallelConsumeOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                result = consume_contact(
                    session,
                    chat_identity=chat_identity,
                    sender_identity=sender_identity,
                    contact_phone=phone,
                    now=verify_at_by_label[label],
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

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(worker, label) for label in ("first", "second")]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            token_lock_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel contact verification timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    winners = [outcome for outcome in outcomes if outcome.kind == "consumed"]
    losers = [outcome for outcome in outcomes if outcome.kind == "domain_error"]
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)
    captured_log = caplog.text
    caplog.clear()

    with session_factory() as session:
        stored_token = session.get(TelegramLinkToken, token_id)
        links = tuple(
            session.scalars(
                select(TelegramLink).where(TelegramLink.user_id == user_id)
            ).all()
        )
        events = tuple(
            session.scalars(
                select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
            ).all()
        )
        assert session.scalar(select(1)) == 1

    assert unexpected == []
    assert len(winners) == 1
    assert winners[0].outcome is TelegramLinkOutcome.LINKED
    assert len(losers) == 1
    assert losers[0].error_code is ErrorCode.LINK_TOKEN_INVALID
    assert all(outcome.session_usable for outcome in outcomes)
    assert stored_token is not None
    assert stored_token.consumed_at == winners[0].consumed_at
    assert stored_token.pending_contact_binding_mac is None
    assert stored_token.contact_requested_at is None
    assert len(links) == 1
    assert links[0].linked_at == links[0].phone_verified_at
    assert links[0].linked_at == winners[0].consumed_at
    assert links[0].unlinked_at is None
    assert len(events) == 1
    assert events[0].action == "linked"
    assert events[0].occurred_at == winners[0].consumed_at
    assert_sensitive_values_absent(
        (
            raw_value,
            phone,
            str(chat_identity.as_bigint()),
            _CONTACT_BINDING_KEY.get_secret_value(),
        ),
        outcome_text,
        captured_log,
    )


@pytest.mark.integration
def test_same_chat_two_user_parallel_contact_keeps_loser_binding_pending(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    raw_value_by_label = {
        "user_a": "parallel_same_chat_user_a_contact",
        "user_b": "parallel_same_chat_user_b_contact",
    }
    phone_by_label = {
        "user_a": "+998900017201",
        "user_b": "+998900017202",
    }
    sender_by_label = {
        "user_a": TelegramUserIdentity(27_201),
        "user_b": TelegramUserIdentity(27_202),
    }
    shared_chat = VerifiedPrivateTelegramChatIdentity(17_201)
    issued_at = datetime(2026, 8, 2, 20, 10, tzinfo=UTC)
    with session_factory.begin() as session:
        user_id_by_label = {}
        token_id_by_label = {}
        for label in ("user_a", "user_b"):
            user, token = add_user_and_token(
                session,
                phone=phone_by_label[label],
                raw_token=RawTelegramLinkToken(raw_value_by_label[label]),
                created_at=issued_at,
            )
            user_id_by_label[label] = user.id
            token_id_by_label[label] = token.id
            bind_contact(
                session,
                raw_token=raw_value_by_label[label],
                chat_identity=shared_chat,
                sender_identity=sender_by_label[label],
                now=issued_at + timedelta(seconds=1),
            )

    verify_at_by_label = {
        "user_a": issued_at + timedelta(seconds=2),
        "user_b": issued_at + timedelta(seconds=3),
    }
    link_mutation_barrier = Barrier(2)
    original_link_mutation = (
        telegram_service.link_phone_verified_private_chat_from_prelocked_state
    )

    def synchronized_link_mutation(*args, **kwargs):
        link_mutation_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return original_link_mutation(*args, **kwargs)

    monkeypatch.setattr(
        telegram_service,
        "link_phone_verified_private_chat_from_prelocked_state",
        synchronized_link_mutation,
    )

    def worker(label: str) -> ParallelConsumeOutcome:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                result = consume_contact(
                    session,
                    chat_identity=shared_chat,
                    sender_identity=sender_by_label[label],
                    contact_phone=phone_by_label[label],
                    now=verify_at_by_label[label],
                )
            except TelegramChatAlreadyLinkedError as exc:
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

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(worker, label) for label in ("user_a", "user_b")]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            link_mutation_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel contact chat collision timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    winners = [outcome for outcome in outcomes if outcome.kind == "consumed"]
    losers = [outcome for outcome in outcomes if outcome.kind == "domain_error"]
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)
    captured_log = caplog.text
    caplog.clear()

    with session_factory() as session:
        tokens = {
            label: session.get(TelegramLinkToken, token_id)
            for label, token_id in token_id_by_label.items()
        }
        links = tuple(
            session.scalars(
                select(TelegramLink).where(TelegramLink.unlinked_at.is_(None))
            ).all()
        )
        events = tuple(
            session.scalars(select(TelegramLinkEvent).order_by(TelegramLinkEvent.id))
        )

    assert unexpected == []
    assert len(winners) == 1
    assert winners[0].outcome is TelegramLinkOutcome.LINKED
    assert len(losers) == 1
    assert losers[0].error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert all(outcome.session_usable for outcome in outcomes)
    assert len(links) == 1
    assert links[0].telegram_chat_id == shared_chat.as_bigint()
    if links[0].user_id != user_id_by_label[winners[0].label]:
        pytest.fail("chat-collision winner ownership mismatch", pytrace=False)
    assert links[0].linked_at == links[0].phone_verified_at
    assert len(events) == 1
    assert events[0].action == "linked"
    winner_token = tokens[winners[0].label]
    loser_token = tokens[losers[0].label]
    assert winner_token is not None
    assert winner_token.consumed_at == winners[0].consumed_at
    assert winner_token.pending_contact_binding_mac is None
    assert loser_token is not None
    assert loser_token.consumed_at is None
    assert loser_token.invalidated_at is None
    assert loser_token.pending_contact_binding_mac is not None
    assert loser_token.contact_requested_at is not None
    assert_sensitive_values_absent(
        (
            *raw_value_by_label.values(),
            *phone_by_label.values(),
            str(shared_chat.as_bigint()),
            _CONTACT_BINDING_KEY.get_secret_value(),
        ),
        outcome_text,
        captured_log,
    )


def _seed_token_first_barrier_case(
    session: Session,
    *,
    case_number: int,
    now: datetime,
) -> tuple[
    User,
    TelegramLink,
    TelegramLinkToken,
    OtpChallenge,
    OtpDispatch,
    VerifiedPrivateTelegramChatIdentity,
    TelegramUserIdentity,
]:
    raw_token_value = f"r07_token_first_barrier_{case_number}"
    user = User(phone=f"+9989000173{case_number:02d}")
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=17_300 + case_number,
        linked_at=now,
        phone_verified_at=now,
        updated_at=now,
    )
    session.add(link)
    session.flush()
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token_value)),
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    challenge = OtpChallenge(
        user_id=user.id,
        purpose=OtpPurpose.LOGIN.value,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=f"{case_number:x}" * 64,
        status=OtpChallengeStatus.PENDING_DISPATCH.value,
        failed_attempts=0,
        created_at=now,
        updated_at=now,
    )
    session.add_all((token, challenge))
    session.flush()
    dispatch = OtpDispatch(
        challenge_id=challenge.id,
        status=OtpDispatchStatus.PENDING.value,
        locale="uz-Latn",
        created_at=now,
        updated_at=now,
    )
    session.add(dispatch)
    session.flush()
    contact_chat = VerifiedPrivateTelegramChatIdentity(17_400 + case_number)
    contact_sender = TelegramUserIdentity(17_400 + case_number)
    bind_contact(
        session,
        raw_token=raw_token_value,
        chat_identity=contact_chat,
        sender_identity=contact_sender,
        now=now + timedelta(microseconds=1),
    )
    return user, link, token, challenge, dispatch, contact_chat, contact_sender


@pytest.mark.integration
@pytest.mark.parametrize(
    ("case_number", "operations"),
    (
        (1, ("contact", "unlink")),
        (2, ("contact", "dispatcher")),
        (3, ("unlink", "dispatcher")),
    ),
)
def test_token_first_contact_unlink_relink_and_dispatcher_barriers_never_deadlock(
    monkeypatch,
    m2_test_database: Engine,
    case_number: int,
    operations: tuple[str, str],
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 20, 20 + case_number, tzinfo=UTC)
    with session_factory.begin() as session:
        user, link, token, challenge, dispatch, contact_chat, contact_sender = (
            _seed_token_first_barrier_case(
                session,
                case_number=case_number,
                now=now,
            )
        )
        user_id = user.id
        user_phone = user.phone
        link_id = link.id
        token_id = token.id
        challenge_id = challenge.id
        dispatch_id = dispatch.id
        original_chat_id = link.telegram_chat_id

    token_locked = Event()
    contending_token_lock_entered = Event()
    dispatch_locked = Event()
    link_path_otp_lock_entered = Event()
    original_contact_lock = telegram_service.lock_telegram_link_token_set_by_ids
    original_unlink_lock = (
        telegram_service.lock_outstanding_telegram_link_token_set_by_user
    )
    original_dispatch_lock = otp_dispatch_service.claim_next_pending_dispatch_for_update
    original_link_path_otp_lock = telegram_service._lock_link_change_otp_state

    def wait_for(event: Event) -> None:
        if not event.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
            raise BrokenBarrierError

    def synchronized_contact_lock(*args, **kwargs):
        locked = original_contact_lock(*args, **kwargs)
        token_locked.set()
        if "unlink" in operations:
            wait_for(contending_token_lock_entered)
        else:
            wait_for(dispatch_locked)
        return locked

    def synchronized_unlink_lock(*args, **kwargs):
        if "contact" in operations:
            wait_for(token_locked)
            contending_token_lock_entered.set()
            return original_unlink_lock(*args, **kwargs)
        locked = original_unlink_lock(*args, **kwargs)
        token_locked.set()
        wait_for(dispatch_locked)
        return locked

    def synchronized_dispatch_lock(*args, **kwargs):
        wait_for(token_locked)
        locked = original_dispatch_lock(*args, **kwargs)
        dispatch_locked.set()
        wait_for(link_path_otp_lock_entered)
        return locked

    def synchronized_link_path_otp_lock(*args, **kwargs):
        wait_for(dispatch_locked)
        link_path_otp_lock_entered.set()
        return original_link_path_otp_lock(*args, **kwargs)

    if "contact" in operations:
        monkeypatch.setattr(
            telegram_service,
            "lock_telegram_link_token_set_by_ids",
            synchronized_contact_lock,
        )
    if "unlink" in operations:
        monkeypatch.setattr(
            telegram_service,
            "lock_outstanding_telegram_link_token_set_by_user",
            synchronized_unlink_lock,
        )
    if "dispatcher" in operations:
        monkeypatch.setattr(
            otp_dispatch_service,
            "claim_next_pending_dispatch_for_update",
            synchronized_dispatch_lock,
        )
        monkeypatch.setattr(
            telegram_service,
            "_lock_link_change_otp_state",
            synchronized_link_path_otp_lock,
        )

    def worker(operation: str) -> tuple[str, str]:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            if operation == "contact":
                try:
                    consume_contact(
                        session,
                        chat_identity=contact_chat,
                        sender_identity=contact_sender,
                        contact_phone=user_phone,
                        now=now + timedelta(seconds=2),
                    )
                    outcome = "CONTACT_VERIFIED"
                except TelegramLinkTokenConsumeError:
                    outcome = ErrorCode.LINK_TOKEN_INVALID.value
            elif operation == "unlink":
                current_user = session.get(User, user_id)
                assert current_user is not None
                try:
                    unlink(session, current_user, now + timedelta(seconds=3))
                    outcome = "UNLINKED"
                except TelegramLinkTokenIssueError as exc:
                    outcome = exc.error_code.value
            else:
                prepared = prepare_next_otp_dispatch(
                    session,
                    otp_hmac_key=SecretStr(
                        "r07-barrier-otp-hmac-key-at-least-32-characters"
                    ),
                    now=now + timedelta(seconds=1),
                    ttl_seconds=300,
                    claim_stale_seconds=60,
                    code_generator=lambda _upper_bound: 123456,
                )
                outcome = "PREPARED" if prepared is not None else "NO_PENDING"
            session.commit()
            return operation, outcome
        except BrokenBarrierError:
            session.rollback()
            return operation, "BROKEN_BARRIER"
        except Exception as exc:
            session.rollback()
            return operation, type(exc).__name__
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(worker, operation) for operation in operations]
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            token_locked.set()
            contending_token_lock_entered.set()
            dispatch_locked.set()
            link_path_otp_lock_entered.set()
            for future in not_done:
                future.cancel()
            pytest.fail("token-first Telegram barrier timed out", pytrace=False)
        outcomes = dict(future.result(timeout=0) for future in futures)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    expected_outcomes = {
        ("contact", "unlink"): {
            "contact": "CONTACT_VERIFIED",
            "unlink": "UNLINKED",
        },
        ("contact", "dispatcher"): {
            "contact": "CONTACT_VERIFIED",
            "dispatcher": "PREPARED",
        },
        ("unlink", "dispatcher"): {
            "unlink": "UNLINKED",
            "dispatcher": "PREPARED",
        },
    }
    assert outcomes == expected_outcomes[operations]

    with session_factory() as session:
        stored_link = session.get(TelegramLink, link_id)
        stored_token = session.get(TelegramLinkToken, token_id)
        stored_challenge = session.get(OtpChallenge, challenge_id)
        stored_dispatch = session.get(OtpDispatch, dispatch_id)
        event_actions = tuple(
            session.scalars(
                select(TelegramLinkEvent.action)
                .where(TelegramLinkEvent.user_id == user_id)
                .order_by(TelegramLinkEvent.occurred_at, TelegramLinkEvent.id)
            ).all()
        )

    assert stored_link is not None
    assert stored_token is not None
    assert stored_challenge is not None
    assert stored_dispatch is not None
    assert stored_challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert stored_dispatch.status == OtpDispatchStatus.CANCELLED.value
    if "unlink" in operations:
        assert stored_link.telegram_chat_id is None
        assert stored_link.unlinked_at is not None
        assert stored_link.phone_verified_at is None
        if "contact" in operations:
            assert stored_token.consumed_at is not None
            assert stored_token.invalidated_at is None
            assert event_actions == ("relinked", "unlinked")
        else:
            assert stored_token.consumed_at is None
            assert stored_token.invalidated_at is not None
            assert event_actions == ("unlinked",)
    else:
        assert stored_link.telegram_chat_id != original_chat_id
        assert stored_link.unlinked_at is None
        assert stored_link.phone_verified_at == stored_link.linked_at
        assert stored_token.consumed_at is not None
        assert stored_token.invalidated_at is None
        assert event_actions == ("relinked",)


@pytest.mark.integration
def test_same_pending_binding_mac_has_exactly_one_winner(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 20, 40, tzinfo=UTC)
    raw_tokens = {
        "first": "same_binding_first_token",
        "second": "same_binding_second_token",
    }
    with session_factory.begin() as session:
        token_ids = {}
        for index, label in enumerate(("first", "second"), start=1):
            user, token = add_user_and_token(
                session,
                phone=f"+9989000174{index:02d}",
                raw_token=RawTelegramLinkToken(raw_tokens[label]),
                created_at=now,
            )
            token_ids[label] = token.id
            assert user.id is not None

    mutation_barrier = Barrier(2)
    original_mutation = telegram_service.bind_locked_telegram_link_token_for_contact

    def synchronized_mutation(*args, **kwargs):
        mutation_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return original_mutation(*args, **kwargs)

    monkeypatch.setattr(
        telegram_service,
        "bind_locked_telegram_link_token_for_contact",
        synchronized_mutation,
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(17_501)
    sender_identity = TelegramUserIdentity(27_501)

    def worker(label: str) -> tuple[str, str, bool]:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                bind_start_token_for_contact(
                    session,
                    RawTelegramLinkToken(raw_tokens[label]),
                    chat_identity,
                    sender_identity,
                    rate_limit_hmac_key=_CONTACT_BINDING_KEY,
                    now=now + timedelta(seconds=1),
                )
                outcome = "BOUND"
            except TelegramLinkTokenConsumeError as exc:
                outcome = exc.error_code.value
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return label, outcome, session_usable
        except BrokenBarrierError:
            session.rollback()
            return label, "BROKEN_BARRIER", False
        except Exception as exc:
            session.rollback()
            return label, type(exc).__name__, False
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(worker, label) for label in ("first", "second")]
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            mutation_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("same contact binding barrier timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert sorted(outcome for _label, outcome, _usable in outcomes) == [
        "BOUND",
        ErrorCode.LINK_TOKEN_INVALID.value,
    ]
    assert all(usable for _label, _outcome, usable in outcomes)

    with session_factory() as session:
        tokens = {
            label: session.get(TelegramLinkToken, token_id)
            for label, token_id in token_ids.items()
        }
        pending_count = (
            session.scalar(
                select(func.count())
                .select_from(TelegramLinkToken)
                .where(TelegramLinkToken.pending_contact_binding_mac.is_not(None))
            )
            or 0
        )
        link_count = session.scalar(select(func.count()).select_from(TelegramLink))
        event_count = session.scalar(
            select(func.count()).select_from(TelegramLinkEvent)
        )

    assert pending_count == 1
    assert link_count == 0
    assert event_count == 0
    assert all(token is not None for token in tokens.values())
    assert all(token.consumed_at is None for token in tokens.values())
    assert all(token.invalidated_at is None for token in tokens.values())


@pytest.mark.integration
def test_contact_empty_otp_snapshot_rejects_concurrently_issued_login_challenge(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 20, 50, tzinfo=UTC)
    raw_token_value = "empty_otp_snapshot_login_issue_token"
    phone = "+998900017601"
    old_chat = VerifiedPrivateTelegramChatIdentity(17_601)
    contact_chat = VerifiedPrivateTelegramChatIdentity(17_602)
    contact_sender = TelegramUserIdentity(17_602)
    with session_factory.begin() as session:
        user, token = add_user_and_token(
            session,
            phone=phone,
            raw_token=RawTelegramLinkToken(raw_token_value),
            created_at=now,
        )
        link = TelegramLink(
            user_id=user.id,
            telegram_chat_id=old_chat.as_bigint(),
            linked_at=now,
            phone_verified_at=now,
            updated_at=now,
        )
        session.add(link)
        session.flush()
        bind_contact(
            session,
            raw_token=raw_token_value,
            chat_identity=contact_chat,
            sender_identity=contact_sender,
            now=now + timedelta(microseconds=1),
        )
        user_id = user.id
        token_id = token.id
        link_id = link.id
        original_link_state = (
            link.telegram_chat_id,
            link.linked_at,
            link.unlinked_at,
            link.phone_verified_at,
            link.updated_at,
        )
        original_token_binding_state = (
            token.pending_contact_binding_mac,
            token.contact_requested_at,
        )

    settings = Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=m2_test_database.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key="r07-phantom-rate-limit-key-at-least-32-characters",
        otp_hmac_key="r07-phantom-otp-hmac-key-at-least-32-characters",
    )
    empty_otp_snapshot_taken = Event()
    login_issue_committed = Event()
    original_otp_lock = telegram_service._lock_link_change_otp_state

    def hold_after_empty_otp_snapshot(*args, **kwargs):
        locked = original_otp_lock(*args, **kwargs)
        if locked.dispatches or locked.challenges:
            raise AssertionError("expected an empty OTP snapshot")
        empty_otp_snapshot_taken.set()
        if not login_issue_committed.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
            raise BrokenBarrierError
        return locked

    monkeypatch.setattr(
        telegram_service,
        "_lock_link_change_otp_state",
        hold_after_empty_otp_snapshot,
    )

    def contact_worker() -> tuple[str, bool]:
        session = session_factory()
        try:
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                consume_contact(
                    session,
                    chat_identity=contact_chat,
                    sender_identity=contact_sender,
                    contact_phone=phone,
                    now=now + timedelta(seconds=2),
                )
            except TelegramLinkTokenConsumeError as exc:
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return exc.error_code.value, session_usable
            session.commit()
            return "CONTACT_VERIFIED", True
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER", False
        except Exception as exc:
            session.rollback()
            return type(exc).__name__, False
        finally:
            session.close()

    def issue_worker() -> str:
        session = session_factory()
        try:
            if not empty_otp_snapshot_taken.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
                raise BrokenBarrierError
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            result = issue_login_otp_in_transaction(
                session,
                settings,
                phone_input=phone,
                browser_binding_digest="6" * 64,
                locale="uz-Latn",
                now=now + timedelta(seconds=1),
            )
            session.commit()
            return result.outcome.value
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER"
        except Exception as exc:
            session.rollback()
            return type(exc).__name__
        finally:
            login_issue_committed.set()
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        contact_future = executor.submit(contact_worker)
        issue_future = executor.submit(issue_worker)
        done, not_done = wait(
            (contact_future, issue_future),
            timeout=_FUTURE_TIMEOUT_SECONDS,
        )
        if not_done:
            empty_otp_snapshot_taken.set()
            login_issue_committed.set()
            for future in not_done:
                future.cancel()
            pytest.fail("empty OTP snapshot barrier timed out", pytrace=False)
        contact_outcome = contact_future.result(timeout=0)
        issue_outcome = issue_future.result(timeout=0)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert contact_outcome == (ErrorCode.LINK_TOKEN_INVALID.value, True)
    assert issue_outcome == OtpInternalOutcome.OTP_PENDING.value

    with session_factory() as session:
        stored_token = session.get(TelegramLinkToken, token_id)
        stored_link = session.get(TelegramLink, link_id)
        challenges = tuple(
            session.scalars(
                select(OtpChallenge)
                .where(
                    OtpChallenge.user_id == user_id,
                    OtpChallenge.purpose == OtpPurpose.LOGIN.value,
                )
                .order_by(OtpChallenge.id)
            ).all()
        )
        dispatches = tuple(
            session.scalars(select(OtpDispatch).order_by(OtpDispatch.id)).all()
        )
        link_event_count = (
            session.scalar(
                select(func.count())
                .select_from(TelegramLinkEvent)
                .where(TelegramLinkEvent.user_id == user_id)
            )
            or 0
        )

    assert stored_token is not None
    assert stored_token.consumed_at is None
    assert stored_token.invalidated_at is None
    assert stored_token.pending_contact_binding_mac is not None
    assert stored_token.contact_requested_at is not None
    if (
        stored_token.pending_contact_binding_mac,
        stored_token.contact_requested_at,
    ) != original_token_binding_state:
        pytest.fail("failed contact changed its pending token", pytrace=False)
    assert stored_link is not None
    if (
        stored_link.telegram_chat_id,
        stored_link.linked_at,
        stored_link.unlinked_at,
        stored_link.phone_verified_at,
        stored_link.updated_at,
    ) != original_link_state:
        pytest.fail("failed contact changed the verified link", pytrace=False)
    assert len(challenges) == 1
    assert len(dispatches) == 1
    challenge = challenges[0]
    dispatch = dispatches[0]
    assert challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert challenge.failed_attempts == 0
    assert dispatch.status == OtpDispatchStatus.PENDING.value
    if (
        challenge.telegram_link_id != link_id
        or challenge.telegram_linked_at != original_link_state[1]
        or dispatch.challenge_id != challenge.id
    ):
        pytest.fail(
            "issued LOGIN challenge lost its old link generation", pytrace=False
        )
    assert link_event_count == 0


@pytest.mark.integration
@pytest.mark.parametrize("winner", ("bind", "unlink"))
def test_start_pending_bind_and_unlink_barrier_converges_token_first(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    winner: str,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 21, 0, tzinfo=UTC)
    raw_token = "start_bind_unlink_barrier_token"
    chat_identity = VerifiedPrivateTelegramChatIdentity(17_701)
    sender_identity = TelegramUserIdentity(27_701)
    with session_factory.begin() as session:
        user, token = add_user_and_token(
            session,
            phone="+998900017701",
            raw_token=RawTelegramLinkToken(raw_token),
            created_at=now,
        )
        link = add_verified_link(
            session,
            user=user,
            chat_id=17_700,
            linked_at=now,
        )
        user_id = user.id
        token_id = token.id
        link_id = link.id

    start_barrier = Barrier(2)
    winner_lock_owned = Event()
    contender_entered = Event()
    original_bind_lock = telegram_service.lock_telegram_link_token_set_by_ids
    original_unlink_lock = (
        telegram_service.lock_outstanding_telegram_link_token_set_by_user
    )

    def wait_for(event: Event) -> None:
        if not event.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
            raise BrokenBarrierError

    def synchronized_bind_lock(*args, **kwargs):
        session = args[0]
        if session.info.get("race_actor") != "bind":
            return original_bind_lock(*args, **kwargs)
        if winner == "bind":
            locked = original_bind_lock(*args, **kwargs)
            winner_lock_owned.set()
            wait_for(contender_entered)
            return locked
        wait_for(winner_lock_owned)
        contender_entered.set()
        return original_bind_lock(*args, **kwargs)

    def synchronized_unlink_lock(*args, **kwargs):
        session = args[0]
        if session.info.get("race_actor") != "unlink":
            return original_unlink_lock(*args, **kwargs)
        if winner == "unlink":
            locked = original_unlink_lock(*args, **kwargs)
            winner_lock_owned.set()
            wait_for(contender_entered)
            return locked
        wait_for(winner_lock_owned)
        contender_entered.set()
        return original_unlink_lock(*args, **kwargs)

    monkeypatch.setattr(
        telegram_service,
        "lock_telegram_link_token_set_by_ids",
        synchronized_bind_lock,
    )
    monkeypatch.setattr(
        telegram_service,
        "lock_outstanding_telegram_link_token_set_by_user",
        synchronized_unlink_lock,
    )

    def bind_worker() -> tuple[str, bool]:
        session = session_factory()
        session.info["race_actor"] = "bind"
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            try:
                bind_contact(
                    session,
                    raw_token=raw_token,
                    chat_identity=chat_identity,
                    sender_identity=sender_identity,
                    now=now + timedelta(seconds=1),
                )
                outcome = "BOUND"
            except TelegramLinkTokenConsumeError as exc:
                outcome = exc.error_code.value
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return outcome, session_usable
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER", False
        except Exception as exc:
            session.rollback()
            return type(exc).__name__, False
        finally:
            session.close()

    def unlink_worker() -> tuple[str, bool]:
        session = session_factory()
        session.info["race_actor"] = "unlink"
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            user = session.get(User, user_id)
            assert user is not None
            result = unlink(session, user, now + timedelta(seconds=2))
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return result.outcome.value, session_usable
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER", False
        except Exception as exc:
            session.rollback()
            return type(exc).__name__, False
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = (executor.submit(bind_worker), executor.submit(unlink_worker))
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            winner_lock_owned.set()
            contender_entered.set()
            for future in not_done:
                future.cancel()
            pytest.fail("start-bind/unlink barrier timed out", pytrace=False)
        bind_outcome = futures[0].result(timeout=0)
        unlink_outcome = futures[1].result(timeout=0)
        assert len(done) == 2
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    expected_bind = "BOUND" if winner == "bind" else ErrorCode.LINK_TOKEN_INVALID.value
    assert bind_outcome == (expected_bind, True)
    assert unlink_outcome == (TelegramLinkOutcome.UNLINKED.value, True)

    with session_factory() as session:
        stored_token = session.get(TelegramLinkToken, token_id)
        stored_link = session.get(TelegramLink, link_id)
        event_actions = tuple(
            session.scalars(
                select(TelegramLinkEvent.action).where(
                    TelegramLinkEvent.user_id == user_id
                )
            )
        )
        challenge_count = session.scalar(select(func.count()).select_from(OtpChallenge))
        dispatch_count = session.scalar(select(func.count()).select_from(OtpDispatch))
        assert stored_token is not None
        assert stored_link is not None

    assert stored_token.consumed_at is None
    assert stored_token.invalidated_at == now + timedelta(seconds=2)
    assert stored_token.pending_contact_binding_mac is None
    assert stored_token.contact_requested_at is None
    assert stored_link.telegram_chat_id is None
    assert stored_link.unlinked_at == now + timedelta(seconds=2)
    assert stored_link.phone_verified_at is None
    assert event_actions == ("unlinked",)
    assert challenge_count == 0
    assert dispatch_count == 0


@pytest.mark.integration
@pytest.mark.parametrize("winner", ("contact", "issue"))
def test_contact_success_and_protected_relink_issue_barrier_converges(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
    winner: str,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 21, 10, tzinfo=UTC)
    old_raw_token = "contact_protected_issue_old_token"
    new_raw_token = "contact_protected_issue_new_token"
    old_chat_id = 17_710
    contact_chat = VerifiedPrivateTelegramChatIdentity(17_711)
    contact_sender = TelegramUserIdentity(27_711)
    with session_factory.begin() as session:
        user, old_token = add_user_and_token(
            session,
            phone="+998900017711",
            raw_token=RawTelegramLinkToken(old_raw_token),
            created_at=now,
        )
        link = add_verified_link(
            session,
            user=user,
            chat_id=old_chat_id,
            linked_at=now,
        )
        customer = add_active_customer(session, user=user, activated_at=now)
        bind_contact(
            session,
            raw_token=old_raw_token,
            chat_identity=contact_chat,
            sender_identity=contact_sender,
            now=now + timedelta(seconds=1),
        )
        user_id = user.id
        old_token_id = old_token.id
        link_id = link.id
        customer_id = customer.id

    start_barrier = Barrier(2)
    winner_lock_owned = Event()
    contender_entered = Event()
    original_contact_lock = telegram_service.lock_telegram_link_token_set_by_ids
    original_issue_mutation = telegram_service.invalidate_and_insert_telegram_link_token

    def wait_for(event: Event) -> None:
        if not event.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
            raise BrokenBarrierError

    def synchronized_contact_lock(*args, **kwargs):
        session = args[0]
        if session.info.get("race_actor") != "contact":
            return original_contact_lock(*args, **kwargs)
        if winner == "contact":
            locked = original_contact_lock(*args, **kwargs)
            winner_lock_owned.set()
            wait_for(contender_entered)
            return locked
        wait_for(winner_lock_owned)
        contender_entered.set()
        return original_contact_lock(*args, **kwargs)

    def synchronized_issue_mutation(*args, **kwargs):
        session = args[0]
        if session.info.get("race_actor") != "issue":
            return original_issue_mutation(*args, **kwargs)
        if winner == "issue":
            token = original_issue_mutation(*args, **kwargs)
            winner_lock_owned.set()
            wait_for(contender_entered)
            return token
        wait_for(winner_lock_owned)
        contender_entered.set()
        return original_issue_mutation(*args, **kwargs)

    monkeypatch.setattr(
        telegram_service,
        "lock_telegram_link_token_set_by_ids",
        synchronized_contact_lock,
    )
    monkeypatch.setattr(
        telegram_service,
        "invalidate_and_insert_telegram_link_token",
        synchronized_issue_mutation,
    )

    def contact_worker() -> tuple[str, bool]:
        session = session_factory()
        session.info["race_actor"] = "contact"
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            try:
                result = consume_contact(
                    session,
                    chat_identity=contact_chat,
                    sender_identity=contact_sender,
                    contact_phone="+998900017711",
                    now=now + timedelta(seconds=2),
                )
                outcome = result.outcome.value
            except TelegramLinkTokenConsumeError as exc:
                outcome = exc.error_code.value
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return outcome, session_usable
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER", False
        except Exception as exc:
            session.rollback()
            return type(exc).__name__, False
        finally:
            session.close()

    def issue_worker() -> tuple[str, object | None, bool]:
        session = session_factory()
        session.info["race_actor"] = "issue"
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            user = session.get(User, user_id)
            assert user is not None
            try:
                issued = issue_relink_token_after_rate_limit(
                    session,
                    user,
                    now + timedelta(seconds=3),
                    token_generator=lambda _size: new_raw_token,
                )
                outcome = "ISSUED"
                token_id: object | None = issued.token.id
            except TelegramLinkTokenIssueError as exc:
                outcome = exc.error_code.value
                token_id = None
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return outcome, token_id, session_usable
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER", None, False
        except Exception as exc:
            session.rollback()
            return type(exc).__name__, None, False
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = (executor.submit(contact_worker), executor.submit(issue_worker))
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            winner_lock_owned.set()
            contender_entered.set()
            for future in not_done:
                future.cancel()
            pytest.fail("contact/protected-issue barrier timed out", pytrace=False)
        contact_outcome = futures[0].result(timeout=0)
        issue_outcome = futures[1].result(timeout=0)
        assert len(done) == 2
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    expected_contact = (
        TelegramLinkOutcome.RELINKED.value
        if winner == "contact"
        else ErrorCode.LINK_TOKEN_INVALID.value
    )
    assert contact_outcome == (expected_contact, True)
    assert issue_outcome[0] == "ISSUED"
    assert issue_outcome[1] is not None
    assert issue_outcome[2] is True
    new_token_id = issue_outcome[1]

    with session_factory() as session:
        old_token = session.get(TelegramLinkToken, old_token_id)
        new_token = session.get(TelegramLinkToken, new_token_id)
        stored_link = session.get(TelegramLink, link_id)
        stored_customer = session.get(Customer, customer_id)
        event_actions = tuple(
            session.scalars(
                select(TelegramLinkEvent.action).where(
                    TelegramLinkEvent.user_id == user_id
                )
            )
        )
        outstanding_count = session.scalar(
            select(func.count())
            .select_from(TelegramLinkToken)
            .where(
                TelegramLinkToken.consumed_at.is_(None),
                TelegramLinkToken.invalidated_at.is_(None),
            )
        )
        assert old_token is not None
        assert new_token is not None
        assert stored_link is not None
        assert stored_customer is not None

    assert new_token.consumed_at is None
    assert new_token.invalidated_at is None
    assert outstanding_count == 1
    assert stored_customer.onboarding_status == "active"
    assert stored_customer.activated_at == now
    if winner == "contact":
        assert old_token.consumed_at == now + timedelta(seconds=2)
        assert old_token.invalidated_at is None
        assert stored_link.telegram_chat_id == contact_chat.as_bigint()
        assert stored_link.linked_at == now + timedelta(seconds=2)
        assert stored_link.phone_verified_at == stored_link.linked_at
        assert event_actions == ("relinked",)
    else:
        assert old_token.consumed_at is None
        assert old_token.invalidated_at == now + timedelta(seconds=3)
        assert old_token.pending_contact_binding_mac is None
        assert old_token.contact_requested_at is None
        assert stored_link.telegram_chat_id == old_chat_id
        assert stored_link.linked_at == now
        assert stored_link.phone_verified_at == now
        assert event_actions == ()


@pytest.mark.integration
def test_simultaneous_protected_relink_token_issue_same_user_has_one_winner(
    monkeypatch: pytest.MonkeyPatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 8, 2, 21, 20, tzinfo=UTC)
    with session_factory.begin() as session:
        user = User(phone="+998900017721")
        session.add(user)
        session.flush()
        link = add_verified_link(
            session,
            user=user,
            chat_id=17_721,
            linked_at=now,
        )
        customer = add_active_customer(session, user=user, activated_at=now)
        user_id = user.id
        link_id = link.id
        customer_id = customer.id
        original_link_state = (
            link.telegram_chat_id,
            link.linked_at,
            link.phone_verified_at,
            link.unlinked_at,
        )

    raw_token_by_label = {
        "first": "parallel_protected_relink_first_token",
        "second": "parallel_protected_relink_second_token",
    }
    empty_snapshot_barrier = Barrier(2)
    original_outstanding_lookup = (
        telegram_repository.get_outstanding_telegram_link_token_for_update
    )

    def synchronize_empty_snapshot(*args, **kwargs):
        token = original_outstanding_lookup(*args, **kwargs)
        if token is not None:
            raise AssertionError("protected issue expected an empty token snapshot")
        empty_snapshot_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        return None

    monkeypatch.setattr(
        telegram_repository,
        "get_outstanding_telegram_link_token_for_update",
        synchronize_empty_snapshot,
    )

    def worker(label: str) -> tuple[str, object | None, bool]:
        session = session_factory()
        try:
            user = session.get(User, user_id)
            assert user is not None
            try:
                issued = issue_relink_token_after_rate_limit(
                    session,
                    user,
                    now + timedelta(seconds=1),
                    token_generator=lambda _size: raw_token_by_label[label],
                )
                outcome = "ISSUED"
                token_id: object | None = issued.token.id
            except TelegramLinkTokenIssueError as exc:
                outcome = exc.error_code.value
                token_id = None
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return outcome, token_id, session_usable
        except BrokenBarrierError:
            session.rollback()
            return "BROKEN_BARRIER", None, False
        except Exception as exc:
            session.rollback()
            return type(exc).__name__, None, False
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(worker, label) for label in ("first", "second")]
        done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            empty_snapshot_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel protected issue barrier timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
        assert len(done) == 2
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert sorted(outcome for outcome, _token_id, _usable in outcomes) == [
        "ISSUED",
        ErrorCode.RATE_LIMITED.value,
    ]
    assert all(usable for _outcome, _token_id, usable in outcomes)
    issued_ids = [
        token_id for outcome, token_id, _usable in outcomes if outcome == "ISSUED"
    ]
    assert len(issued_ids) == 1
    assert issued_ids[0] is not None

    with session_factory() as session:
        tokens = tuple(
            session.scalars(
                select(TelegramLinkToken).where(TelegramLinkToken.user_id == user_id)
            )
        )
        stored_link = session.get(TelegramLink, link_id)
        stored_customer = session.get(Customer, customer_id)
        event_count = session.scalar(
            select(func.count()).select_from(TelegramLinkEvent)
        )
        assert stored_link is not None
        assert stored_customer is not None

    assert len(tokens) == 1
    assert tokens[0].id == issued_ids[0]
    assert tokens[0].consumed_at is None
    assert tokens[0].invalidated_at is None
    assert (
        stored_link.telegram_chat_id,
        stored_link.linked_at,
        stored_link.phone_verified_at,
        stored_link.unlinked_at,
    ) == original_link_state
    assert stored_customer.onboarding_status == "active"
    assert stored_customer.activated_at == now
    assert event_count == 0
