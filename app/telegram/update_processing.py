from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.error_codes import ErrorCode
from app.telegram.bot_api import TelegramUpdateEnvelope
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.polling_repository import (
    advance_next_offset,
    delete_non_quarantined_update_failure,
    lock_polling_state,
    record_update_failure_and_maybe_advance_cursor,
)
from app.telegram.service import (
    TelegramChatAlreadyLinkedError,
    TelegramLinkOutcome,
    TelegramLinkTokenConsumeError,
    consume_start_token,
)
from app.telegram.update_parser import (
    TelegramUpdateParseCode,
    parse_telegram_update,
)

TELEGRAM_TX_FAILURE_CODE: Final = "UNEXPECTED_TX_A_FAILURE"
TELEGRAM_DB_BACKOFF_CAP_SECONDS: Final = 30.0
_TRANSIENT_EXACT_SQLSTATES: Final = frozenset(
    {
        "40001",
        "40P01",
        "55P03",
        "57P01",
        "57P02",
        "57P03",
        "53300",
    }
)
LOGGER = logging.getLogger("nasiya.telegram.update_processing")


class TelegramUpdateOutcomeCode(StrEnum):
    DUPLICATE = "DUPLICATE"
    UNSUPPORTED_UPDATE = "UNSUPPORTED_UPDATE"
    NON_PRIVATE_CHAT = "NON_PRIVATE_CHAT"
    MALFORMED_START = "MALFORMED_START"
    LINKED = "LINKED"
    RELINKED = "RELINKED"
    ALREADY_LINKED_TO_THIS_CHAT = "ALREADY_LINKED_TO_THIS_CHAT"
    LINK_TOKEN_INVALID = "LINK_TOKEN_INVALID"
    LINK_REJECTED = "LINK_REJECTED"
    QUARANTINED = "QUARANTINED"


class BotReplyKey(StrEnum):
    LINKED = "LINKED"
    ALREADY_LINKED = "ALREADY_LINKED"
    LINK_FAILED = "LINK_FAILED"


@dataclass(frozen=True, repr=False)
class BotReplyIntent:
    chat_identity: VerifiedPrivateTelegramChatIdentity
    reply_key: BotReplyKey

    def __repr__(self) -> str:
        return (
            "BotReplyIntent(chat_identity=<redacted>, "
            f"reply_key={self.reply_key.value!r})"
        )


@dataclass(frozen=True)
class TelegramTxAResult:
    outcome: TelegramUpdateOutcomeCode
    reply_intent: BotReplyIntent | None


@dataclass(frozen=True)
class TelegramTxBResult:
    attempt_count: int
    quarantined: bool


class TelegramFailureClass(StrEnum):
    TRANSIENT = "TRANSIENT"
    POISON = "POISON"
    CONTROLLED_CANCELLATION = "CONTROLLED_CANCELLATION"


class TelegramTxBTransientError(RuntimeError):
    pass


class TelegramTxBFatalError(RuntimeError):
    pass


def process_telegram_update_tx_a(
    session_factory: sessionmaker[Session],
    *,
    update: TelegramUpdateEnvelope,
    now: datetime,
) -> TelegramTxAResult:
    current_time = _as_utc(now)
    result: TelegramTxAResult
    with session_factory.begin() as session:
        state = lock_polling_state(session)
        if update.update_id < state.next_offset:
            result = TelegramTxAResult(
                outcome=TelegramUpdateOutcomeCode.DUPLICATE,
                reply_intent=None,
            )
        else:
            result = _apply_terminal_update(
                session,
                update=update,
                now=current_time,
            )
            delete_non_quarantined_update_failure(
                session,
                update_id=update.update_id,
            )
            advance_next_offset(
                session,
                next_offset=update.update_id + 1,
                now=current_time,
            )
    return result


def record_poison_failure_tx_b(
    session_factory: sessionmaker[Session],
    *,
    update_id: int,
    now: datetime,
) -> TelegramTxBResult:
    current_time = _as_utc(now)
    try:
        with session_factory.begin() as session:
            failure = record_update_failure_and_maybe_advance_cursor(
                session,
                update_id=update_id,
                failure_code=TELEGRAM_TX_FAILURE_CODE,
                now=current_time,
            )
            result = TelegramTxBResult(
                attempt_count=failure.attempt_count,
                quarantined=failure.quarantined_at is not None,
            )
    except Exception as exc:
        failure_class = classify_telegram_tx_failure(exc)
        if failure_class is TelegramFailureClass.TRANSIENT:
            raise TelegramTxBTransientError("Telegram TX-B transient failure") from None
        raise TelegramTxBFatalError("Telegram TX-B fatal failure") from None

    if result.quarantined:
        LOGGER.warning("TELEGRAM_UPDATE_QUARANTINED")
    return result


def classify_telegram_tx_failure(
    exc: BaseException,
    *,
    known_statement_timeout: bool = False,
    controlled_cancellation: bool = False,
) -> TelegramFailureClass:
    if controlled_cancellation or isinstance(exc, asyncio.CancelledError):
        return TelegramFailureClass.CONTROLLED_CANCELLATION

    for candidate in _exception_chain(exc):
        if isinstance(candidate, SQLAlchemyTimeoutError):
            return TelegramFailureClass.TRANSIENT
        if isinstance(candidate, DBAPIError) and candidate.connection_invalidated:
            return TelegramFailureClass.TRANSIENT
        sqlstate = _extract_sqlstate(candidate)
        if sqlstate is None:
            continue
        if sqlstate.startswith("08") or sqlstate in _TRANSIENT_EXACT_SQLSTATES:
            return TelegramFailureClass.TRANSIENT
        if sqlstate == "57014" and known_statement_timeout:
            return TelegramFailureClass.TRANSIENT
    return TelegramFailureClass.POISON


class TelegramUpdateProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], Awaitable[bool]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._sleeper = sleeper

    async def __call__(self, update: TelegramUpdateEnvelope):
        from app.telegram.worker import PollingUpdateOutcome

        now = self._now_factory()
        try:
            process_telegram_update_tx_a(
                self._session_factory,
                update=update,
                now=now,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure_class = classify_telegram_tx_failure(exc)
            if failure_class is TelegramFailureClass.TRANSIENT:
                await self._wait(1.0)
                return PollingUpdateOutcome.RETRY

            try:
                tx_b_result = record_poison_failure_tx_b(
                    self._session_factory,
                    update_id=update.update_id,
                    now=now,
                )
            except TelegramTxBTransientError:
                await self._wait(1.0)
                return PollingUpdateOutcome.RETRY
            if tx_b_result.quarantined:
                return PollingUpdateOutcome.TERMINAL
            await self._wait(
                min(
                    float(2 ** (tx_b_result.attempt_count - 1)),
                    TELEGRAM_DB_BACKOFF_CAP_SECONDS,
                )
            )
            return PollingUpdateOutcome.RETRY
        return PollingUpdateOutcome.TERMINAL

    async def _wait(self, seconds: float) -> None:
        if self._sleeper is not None:
            await self._sleeper(seconds)
            return
        await asyncio.sleep(seconds)


def _apply_terminal_update(
    session: Session,
    *,
    update: TelegramUpdateEnvelope,
    now: datetime,
) -> TelegramTxAResult:
    parsed = parse_telegram_update(update)
    parse_outcome = {
        TelegramUpdateParseCode.UNSUPPORTED_UPDATE: (
            TelegramUpdateOutcomeCode.UNSUPPORTED_UPDATE
        ),
        TelegramUpdateParseCode.NON_PRIVATE_CHAT: (
            TelegramUpdateOutcomeCode.NON_PRIVATE_CHAT
        ),
        TelegramUpdateParseCode.MALFORMED_START: (
            TelegramUpdateOutcomeCode.MALFORMED_START
        ),
    }.get(parsed.code)
    if parse_outcome is not None:
        return TelegramTxAResult(outcome=parse_outcome, reply_intent=None)

    if parsed.chat_identity is None or parsed.raw_token is None:
        raise RuntimeError("Private Telegram start parser invariant failed")
    try:
        consumed = consume_start_token(
            session,
            parsed.raw_token,
            parsed.chat_identity,
            now,
        )
    except TelegramLinkTokenConsumeError as exc:
        if exc.error_code is not ErrorCode.LINK_TOKEN_INVALID:
            raise
        return TelegramTxAResult(
            outcome=TelegramUpdateOutcomeCode.LINK_TOKEN_INVALID,
            reply_intent=BotReplyIntent(
                parsed.chat_identity,
                BotReplyKey.LINK_FAILED,
            ),
        )
    except TelegramChatAlreadyLinkedError as exc:
        if exc.error_code is not ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED:
            raise
        return TelegramTxAResult(
            outcome=TelegramUpdateOutcomeCode.LINK_REJECTED,
            reply_intent=BotReplyIntent(
                parsed.chat_identity,
                BotReplyKey.LINK_FAILED,
            ),
        )

    outcome = {
        TelegramLinkOutcome.LINKED: TelegramUpdateOutcomeCode.LINKED,
        TelegramLinkOutcome.RELINKED: TelegramUpdateOutcomeCode.RELINKED,
        TelegramLinkOutcome.ALREADY_LINKED_TO_THIS_CHAT: (
            TelegramUpdateOutcomeCode.ALREADY_LINKED_TO_THIS_CHAT
        ),
    }[consumed.outcome]
    reply_key = (
        BotReplyKey.ALREADY_LINKED
        if consumed.outcome is TelegramLinkOutcome.ALREADY_LINKED_TO_THIS_CHAT
        else BotReplyKey.LINKED
    )
    return TelegramTxAResult(
        outcome=outcome,
        reply_intent=BotReplyIntent(parsed.chat_identity, reply_key),
    )


def _exception_chain(exc: BaseException):
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        yield candidate
        for nested in (
            getattr(candidate, "orig", None),
            candidate.__cause__,
            candidate.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)


def _extract_sqlstate(exc: BaseException) -> str | None:
    for value in (
        getattr(exc, "sqlstate", None),
        getattr(exc, "pgcode", None),
        getattr(getattr(exc, "diag", None), "sqlstate", None),
    ):
        if isinstance(value, str) and len(value) == 5:
            return value.upper()
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Telegram update timestamp must be timezone-aware")
    return value.astimezone(UTC)
