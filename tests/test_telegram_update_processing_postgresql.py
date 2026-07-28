import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.update_processing as processing_module
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.bot_api import (
    TelegramMessageEnvelope,
    TelegramUpdateEnvelope,
)
from app.telegram.models import (
    TelegramLink,
    TelegramLinkEvent,
    TelegramLinkToken,
    TelegramUpdateFailure,
)
from app.telegram.polling_repository import (
    get_next_offset,
    increment_update_failure,
    load_or_create_polling_state,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token
from app.telegram.update_processing import (
    TELEGRAM_TX_FAILURE_CODE,
    BotReplyKey,
    TelegramTxBFatalError,
    TelegramTxBTransientError,
    TelegramUpdateOutcomeCode,
    TelegramUpdateProcessor,
    process_telegram_update_tx_a,
    record_poison_failure_tx_b,
)
from app.telegram.worker import (
    PollingUpdateOutcome,
    ShutdownController,
    run_polling_loop,
)

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=UTC)


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def make_update(
    update_id: int,
    *,
    chat_id: int = 90001,
    text: str = "/start missing_token",
) -> TelegramUpdateEnvelope:
    return TelegramUpdateEnvelope(
        update_id=update_id,
        message=TelegramMessageEnvelope(
            chat_id=chat_id,
            chat_type="private",
            text=text,
            structurally_valid=True,
        ),
    )


def seed_token(
    session: Session,
    *,
    phone: str,
    raw_token: str,
) -> tuple[User, TelegramLinkToken]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    session.add(token)
    session.flush()
    return user, token


def initialize_state(engine: Engine) -> None:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)


@pytest.mark.integration
def test_expected_terminal_invalid_token_advances_without_poison(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        for attempt in range(4):
            increment_update_failure(
                session,
                update_id=10,
                failure_code=TELEGRAM_TX_FAILURE_CODE,
                now=NOW + timedelta(seconds=attempt),
            )

    result = process_telegram_update_tx_a(
        session_factory,
        update=make_update(10),
        now=NOW,
    )

    assert result.outcome is TelegramUpdateOutcomeCode.LINK_TOKEN_INVALID
    assert result.reply_intent is not None
    assert result.reply_intent.reply_key is BotReplyKey.LINK_FAILED
    with session_factory() as session:
        assert get_next_offset(session) == 11
        assert session.get(TelegramUpdateFailure, 10) is None
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("update", "expected_outcome"),
    [
        (
            TelegramUpdateEnvelope(update_id=11),
            TelegramUpdateOutcomeCode.UNSUPPORTED_UPDATE,
        ),
        (
            TelegramUpdateEnvelope(
                update_id=11,
                message=TelegramMessageEnvelope(
                    chat_id=-100,
                    chat_type="group",
                    text="/start group_token",
                    structurally_valid=True,
                ),
            ),
            TelegramUpdateOutcomeCode.NON_PRIVATE_CHAT,
        ),
        (
            TelegramUpdateEnvelope(
                update_id=11,
                message=TelegramMessageEnvelope(
                    chat_id=123,
                    chat_type="private",
                    text="/start malformed token",
                    structurally_valid=True,
                ),
            ),
            TelegramUpdateOutcomeCode.MALFORMED_START,
        ),
    ],
)
def test_parser_a_outcomes_advance_without_reply_or_poison(
    m2_test_database: Engine,
    update: TelegramUpdateEnvelope,
    expected_outcome: TelegramUpdateOutcomeCode,
) -> None:
    initialize_state(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)

    result = process_telegram_update_tx_a(
        session_factory,
        update=update,
        now=NOW,
    )

    assert result.outcome is expected_outcome
    assert result.reply_intent is None
    with session_factory() as session:
        assert get_next_offset(session) == 12
        assert session.get(TelegramUpdateFailure, 11) is None


@pytest.mark.integration
def test_link_event_failure_cleanup_and_cursor_commit_in_one_tx_a(
    m2_test_database: Engine,
) -> None:
    raw_token = "atomic_link_token"
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        user, token = seed_token(
            session,
            phone="+998900080001",
            raw_token=raw_token,
        )
        for attempt in range(4):
            increment_update_failure(
                session,
                update_id=20,
                failure_code=TELEGRAM_TX_FAILURE_CODE,
                now=NOW - timedelta(seconds=4 - attempt),
            )
        user_id = user.id
        token_id = token.id

    result = process_telegram_update_tx_a(
        session_factory,
        update=make_update(
            20,
            chat_id=80001,
            text=f"/start {raw_token}",
        ),
        now=NOW,
    )

    assert result.outcome is TelegramUpdateOutcomeCode.LINKED
    assert result.reply_intent is not None
    assert result.reply_intent.reply_key is BotReplyKey.LINKED
    with session_factory() as session:
        stored_token = session.get(TelegramLinkToken, token_id)
        link = session.scalar(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        )
        event_row = session.scalar(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        )
        assert stored_token is not None
        assert stored_token.consumed_at == NOW
        assert link is not None
        assert link.telegram_chat_id == 80001
        assert event_row is not None
        assert event_row.action == "linked"
        assert session.get(TelegramUpdateFailure, 20) is None
        assert get_next_offset(session) == 21


@pytest.mark.integration
def test_same_chat_is_terminal_idempotent_without_second_event(
    m2_test_database: Engine,
) -> None:
    raw_token = "same_chat_retry_token"
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        user, _token = seed_token(
            session,
            phone="+998900080006",
            raw_token=raw_token,
        )
        session.add(
            TelegramLink(
                user_id=user.id,
                telegram_chat_id=81001,
                linked_at=NOW - timedelta(minutes=2),
                updated_at=NOW - timedelta(minutes=2),
            )
        )

    result = process_telegram_update_tx_a(
        session_factory,
        update=make_update(
            21,
            chat_id=81001,
            text=f"/start {raw_token}",
        ),
        now=NOW,
    )

    assert result.outcome is (TelegramUpdateOutcomeCode.ALREADY_LINKED_TO_THIS_CHAT)
    assert result.reply_intent is not None
    assert result.reply_intent.reply_key is BotReplyKey.ALREADY_LINKED
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 1
        assert session.scalar(select(func.count()).select_from(TelegramLinkEvent)) == 0
        assert get_next_offset(session) == 22


@pytest.mark.integration
def test_chat_collision_is_terminal_no_takeover_with_private_reply_key(
    m2_test_database: Engine,
) -> None:
    raw_token = "collision_candidate_token"
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        owner = User(phone="+998900080007")
        session.add(owner)
        session.flush()
        session.add(
            TelegramLink(
                user_id=owner.id,
                telegram_chat_id=81002,
                linked_at=NOW - timedelta(minutes=2),
                updated_at=NOW - timedelta(minutes=2),
            )
        )
        _candidate, token = seed_token(
            session,
            phone="+998900080008",
            raw_token=raw_token,
        )
        token_id = token.id

    result = process_telegram_update_tx_a(
        session_factory,
        update=make_update(
            22,
            chat_id=81002,
            text=f"/start {raw_token}",
        ),
        now=NOW,
    )

    assert result.outcome is TelegramUpdateOutcomeCode.LINK_REJECTED
    assert result.reply_intent is not None
    assert result.reply_intent.reply_key is BotReplyKey.LINK_FAILED
    with session_factory() as session:
        stored_token = session.get(TelegramLinkToken, token_id)
        assert stored_token is not None
        assert stored_token.consumed_at is None
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 1
        assert session.scalar(select(func.count()).select_from(TelegramLinkEvent)) == 0
        assert get_next_offset(session) == 23


@pytest.mark.integration
def test_cursor_failure_rolls_back_domain_event_token_and_cleanup(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_token = "rollback_atomic_token"
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        _user, token = seed_token(
            session,
            phone="+998900080002",
            raw_token=raw_token,
        )
        increment_update_failure(
            session,
            update_id=30,
            failure_code=TELEGRAM_TX_FAILURE_CODE,
            now=NOW,
        )
        token_id = token.id

    def fail_cursor(*args, **kwargs):
        raise RuntimeError("injected cursor failure")

    monkeypatch.setattr(processing_module, "advance_next_offset", fail_cursor)
    with pytest.raises(RuntimeError, match="injected cursor failure"):
        process_telegram_update_tx_a(
            session_factory,
            update=make_update(
                30,
                chat_id=80002,
                text=f"/start {raw_token}",
            ),
            now=NOW,
        )

    with session_factory() as session:
        token = session.get(TelegramLinkToken, token_id)
        assert token is not None
        assert token.consumed_at is None
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 0
        assert session.scalar(select(func.count()).select_from(TelegramLinkEvent)) == 0
        assert session.get(TelegramUpdateFailure, 30) is not None
        assert get_next_offset(session) == 0

    monkeypatch.undo()
    retry_result = process_telegram_update_tx_a(
        session_factory,
        update=make_update(
            30,
            chat_id=80002,
            text=f"/start {raw_token}",
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert retry_result.outcome is TelegramUpdateOutcomeCode.LINKED
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 1
        assert session.scalar(select(func.count()).select_from(TelegramLinkEvent)) == 1
        assert session.get(TelegramUpdateFailure, 30) is None
        assert get_next_offset(session) == 31


@pytest.mark.integration
def test_crash_after_tx_a_rollback_before_tx_b_never_loses_update(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_state(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)

    def crash_before_tx_b(*args, **kwargs):
        raise RuntimeError("simulated process crash")

    monkeypatch.setattr(
        processing_module,
        "_apply_terminal_update",
        crash_before_tx_b,
    )
    with pytest.raises(RuntimeError, match="simulated process crash"):
        process_telegram_update_tx_a(
            session_factory,
            update=TelegramUpdateEnvelope(update_id=35),
            now=NOW,
        )

    with session_factory() as session:
        assert get_next_offset(session) == 0
        assert session.get(TelegramUpdateFailure, 35) is None


@pytest.mark.integration
def test_commit_failure_returns_no_intent_and_rolls_back(
    m2_test_database: Engine,
) -> None:
    raw_token = "commit_failure_token"
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        _user, token = seed_token(
            session,
            phone="+998900080003",
            raw_token=raw_token,
        )
        token_id = token.id

    def fail_commit(_connection) -> None:
        raise RuntimeError("injected commit failure")

    event.listen(m2_test_database, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected commit failure"):
            process_telegram_update_tx_a(
                session_factory,
                update=make_update(
                    40,
                    chat_id=80003,
                    text=f"/start {raw_token}",
                ),
                now=NOW,
            )
    finally:
        event.remove(m2_test_database, "commit", fail_commit)

    with session_factory() as session:
        token = session.get(TelegramLinkToken, token_id)
        assert token is not None
        assert token.consumed_at is None
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 0
        assert get_next_offset(session) == 0


@pytest.mark.integration
def test_success_deletes_active_failure_but_retains_quarantined_row(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        for attempt in range(5):
            increment_update_failure(
                session,
                update_id=50,
                failure_code=TELEGRAM_TX_FAILURE_CODE,
                now=NOW + timedelta(seconds=attempt),
            )

    result = process_telegram_update_tx_a(
        session_factory,
        update=TelegramUpdateEnvelope(update_id=50),
        now=NOW + timedelta(seconds=6),
    )

    assert result.outcome is TelegramUpdateOutcomeCode.UNSUPPORTED_UPDATE
    with session_factory() as session:
        failure = session.get(TelegramUpdateFailure, 50)
        assert failure is not None
        assert failure.quarantined_at is not None
        assert get_next_offset(session) == 51


@pytest.mark.integration
def test_tx_b_attempts_one_to_five_and_emits_sanitized_quarantine_signal(
    m2_test_database: Engine,
    caplog,
) -> None:
    initialize_state(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)

    for attempt in range(1, 5):
        result = record_poison_failure_tx_b(
            session_factory,
            update_id=60,
            now=NOW + timedelta(seconds=attempt),
        )
        assert result.attempt_count == attempt
        assert result.quarantined is False
        with session_factory() as session:
            assert get_next_offset(session) == 0

    processing_module.LOGGER.disabled = False
    with caplog.at_level(
        logging.WARNING,
        logger=processing_module.LOGGER.name,
    ):
        result = record_poison_failure_tx_b(
            session_factory,
            update_id=60,
            now=NOW + timedelta(seconds=5),
        )

    assert result.attempt_count == 5
    assert result.quarantined is True
    assert "TELEGRAM_UPDATE_QUARANTINED" in caplog.text
    assert "60" not in caplog.text
    with session_factory() as session:
        failure = session.get(TelegramUpdateFailure, 60)
        assert failure is not None
        assert failure.failure_code == TELEGRAM_TX_FAILURE_CODE
        assert failure.quarantined_at is not None
        assert get_next_offset(session) == 61

    next_result = process_telegram_update_tx_a(
        session_factory,
        update=TelegramUpdateEnvelope(update_id=61),
        now=NOW + timedelta(seconds=6),
    )
    assert next_result.outcome is TelegramUpdateOutcomeCode.UNSUPPORTED_UPDATE
    with session_factory() as session:
        assert session.get(TelegramUpdateFailure, 60) is not None
        assert get_next_offset(session) == 62


@pytest.mark.integration
def test_processor_unknown_tx_a_failure_persists_attempts_then_quarantines(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_state(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    sleeps = []

    def fail_tx_a(*args, **kwargs):
        raise RuntimeError("raw unexpected detail")

    async def sleeper(seconds: float) -> bool:
        sleeps.append(seconds)
        return False

    monkeypatch.setattr(
        processing_module,
        "process_telegram_update_tx_a",
        fail_tx_a,
    )
    processor = TelegramUpdateProcessor(
        session_factory,
        now_factory=lambda: NOW,
        sleeper=sleeper,
    )
    update = make_update(70, text="/start raw_sensitive_token")

    for attempt in range(4):
        assert run(processor(update)) is PollingUpdateOutcome.RETRY
        with session_factory() as session:
            failure = session.get(TelegramUpdateFailure, 70)
            assert failure is not None
            assert failure.attempt_count == attempt + 1
            assert get_next_offset(session) == 0

    assert run(processor(update)) is PollingUpdateOutcome.TERMINAL
    assert sleeps == [1, 2, 4, 8]
    with session_factory() as session:
        failure = session.get(TelegramUpdateFailure, 70)
        assert failure is not None
        assert failure.attempt_count == 5
        assert failure.failure_code == TELEGRAM_TX_FAILURE_CODE
        assert get_next_offset(session) == 71


@pytest.mark.integration
def test_tx_b_transient_and_unknown_failures_are_distinct(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_state(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)

    class SqlstateError(RuntimeError):
        sqlstate = "40001"

    monkeypatch.setattr(
        processing_module,
        "record_update_failure_and_maybe_advance_cursor",
        lambda *args, **kwargs: (_ for _ in ()).throw(SqlstateError()),
    )
    with pytest.raises(TelegramTxBTransientError):
        record_poison_failure_tx_b(
            session_factory,
            update_id=80,
            now=NOW,
        )

    monkeypatch.setattr(
        processing_module,
        "record_update_failure_and_maybe_advance_cursor",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unknown")),
    )
    with pytest.raises(TelegramTxBFatalError):
        record_poison_failure_tx_b(
            session_factory,
            update_id=80,
            now=NOW,
        )
    with session_factory() as session:
        assert session.get(TelegramUpdateFailure, 80) is None
        assert get_next_offset(session) == 0


@pytest.mark.integration
def test_out_of_order_batch_duplicate_and_restart_mutate_domain_once(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_token = "ordered_first_token"
    second_token = "ordered_second_token"
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        seed_token(
            session,
            phone="+998900080004",
            raw_token=first_token,
        )
        seed_token(
            session,
            phone="+998900080005",
            raw_token=second_token,
        )

    shutdown = ShutdownController()
    updates = (
        make_update(91, chat_id=80005, text=f"/start {second_token}"),
        make_update(90, chat_id=80004, text=f"/start {first_token}"),
        make_update(90, chat_id=80004, text=f"/start {first_token}"),
    )

    class FakeClient:
        async def get_updates(self, **_kwargs):
            return updates

    base_processor = TelegramUpdateProcessor(
        session_factory,
        now_factory=lambda: NOW,
    )
    processed = []

    async def processor(update: TelegramUpdateEnvelope):
        processed.append(update.update_id)
        outcome = await base_processor(update)
        if len(processed) == 2:
            shutdown.request()
        return outcome

    run(
        run_polling_loop(
            FakeClient(),  # type: ignore[arg-type]
            session_factory=session_factory,
            processor=processor,
            shutdown=shutdown,
        )
    )

    duplicate_result = process_telegram_update_tx_a(
        session_factory,
        update=updates[0],
        now=NOW + timedelta(seconds=1),
    )
    assert processed == [90, 91]
    assert duplicate_result.outcome is TelegramUpdateOutcomeCode.DUPLICATE
    with session_factory() as session:
        assert get_next_offset(session) == 92
        assert session.scalar(select(func.count()).select_from(TelegramLink)) == 2
        assert session.scalar(select(func.count()).select_from(TelegramLinkEvent)) == 2


def test_processing_source_never_logs_or_persists_raw_failure_material() -> None:
    source = processing_module.__loader__.get_source(  # type: ignore[union-attr]
        processing_module.__name__
    )
    assert source is not None
    assert "exception_text" not in source
    assert "raw_update" not in source
    assert "traceback" not in source
    assert "failure_code=str" not in source
    assert "logger.exception" not in source.casefold()
