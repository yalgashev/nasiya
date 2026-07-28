import asyncio
import inspect
from collections.abc import Awaitable

import pytest

import app.telegram.bot_reply as bot_reply_module
from app.telegram.bot_api import TelegramApiError, TelegramApiErrorCode
from app.telegram.bot_reply import (
    BotReplyDeliveryStatus,
    deliver_bot_reply_best_effort,
    render_bot_reply,
)
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.update_processing import (
    BotReplyIntent,
    BotReplyKey,
    TelegramReplyLanguage,
)


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def intent(
    key: BotReplyKey,
    language: TelegramReplyLanguage,
) -> BotReplyIntent:
    return BotReplyIntent(
        chat_identity=VerifiedPrivateTelegramChatIdentity(991122),
        reply_key=key,
        language=language,
    )


class FakeClient:
    def __init__(self, outcomes: list[BaseException | None]) -> None:
        self.outcomes = outcomes
        self.calls = []

    async def send_message(self, *, chat_id, text) -> None:
        self.calls.append((chat_id, text))
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


@pytest.mark.parametrize("language", list(TelegramReplyLanguage))
@pytest.mark.parametrize("key", list(BotReplyKey))
def test_reply_catalog_is_complete_localized_and_privacy_safe(
    language: TelegramReplyLanguage,
    key: BotReplyKey,
) -> None:
    message = render_bot_reply(intent(key, language))

    assert message
    assert "Nasiya" in message
    for forbidden in (
        "token",
        "chat",
        "account",
        "phone",
        "collision",
        "expired",
        "replay",
        "invalid",
        "991122",
    ):
        assert forbidden not in message.casefold()


def test_invalid_expired_replay_and_collision_share_one_failure_message() -> None:
    for language in TelegramReplyLanguage:
        invalid_message = render_bot_reply(intent(BotReplyKey.LINK_FAILED, language))
        collision_message = render_bot_reply(intent(BotReplyKey.LINK_FAILED, language))
        assert invalid_message == collision_message


def test_success_and_no_reply_delivery() -> None:
    client = FakeClient([None])

    assert (
        run(
            deliver_bot_reply_best_effort(
                client,  # type: ignore[arg-type]
                intent=intent(BotReplyKey.LINKED, TelegramReplyLanguage.UZ_LATN),
            )
        )
        is BotReplyDeliveryStatus.SENT
    )
    assert (
        run(
            deliver_bot_reply_best_effort(
                client,  # type: ignore[arg-type]
                intent=None,
            )
        )
        is BotReplyDeliveryStatus.NO_REPLY
    )
    assert len(client.calls) == 1


def test_rate_limit_waits_and_retries_exactly_once() -> None:
    client = FakeClient(
        [
            TelegramApiError(
                TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
                retry_after_seconds=17,
            ),
            None,
        ]
    )
    sleeps = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    result = run(
        deliver_bot_reply_best_effort(
            client,  # type: ignore[arg-type]
            intent=intent(BotReplyKey.LINKED, TelegramReplyLanguage.RU),
            sleeper=sleeper,
        )
    )

    assert result is BotReplyDeliveryStatus.SENT
    assert sleeps == [17]
    assert len(client.calls) == 2


def test_second_rate_limit_or_unknown_timeout_is_not_retried_again() -> None:
    second_rate_limit = TelegramApiError(
        TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
        retry_after_seconds=3,
    )
    client = FakeClient(
        [
            TelegramApiError(
                TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
                retry_after_seconds=2,
            ),
            second_rate_limit,
        ]
    )
    sleeps = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    result = run(
        deliver_bot_reply_best_effort(
            client,  # type: ignore[arg-type]
            intent=intent(BotReplyKey.LINK_FAILED, TelegramReplyLanguage.UZ_LATN),
            sleeper=sleeper,
        )
    )
    assert result is BotReplyDeliveryStatus.NOT_SENT
    assert sleeps == [2]
    assert len(client.calls) == 2

    timeout_client = FakeClient(
        [TelegramApiError(TelegramApiErrorCode.TRANSIENT_NETWORK)]
    )
    result = run(
        deliver_bot_reply_best_effort(
            timeout_client,  # type: ignore[arg-type]
            intent=intent(BotReplyKey.LINKED, TelegramReplyLanguage.UZ_LATN),
            sleeper=sleeper,
        )
    )
    assert result is BotReplyDeliveryStatus.NOT_SENT
    assert len(timeout_client.calls) == 1


def test_rate_limit_wait_interrupted_by_shutdown_does_not_send_again() -> None:
    client = FakeClient(
        [
            TelegramApiError(
                TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
                retry_after_seconds=120,
            )
        ]
    )
    sleeps = []

    async def interrupted_sleeper(seconds: float) -> bool:
        sleeps.append(seconds)
        return True

    result = run(
        deliver_bot_reply_best_effort(
            client,  # type: ignore[arg-type]
            intent=intent(BotReplyKey.LINKED, TelegramReplyLanguage.UZ_LATN),
            sleeper=interrupted_sleeper,
        )
    )

    assert result is BotReplyDeliveryStatus.NOT_SENT
    assert sleeps == [60]
    assert len(client.calls) == 1


def test_delivery_cancellation_propagates_without_retry() -> None:
    client = FakeClient([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        run(
            deliver_bot_reply_best_effort(
                client,  # type: ignore[arg-type]
                intent=intent(BotReplyKey.LINKED, TelegramReplyLanguage.UZ_LATN),
            )
        )
    assert len(client.calls) == 1


def test_reply_module_has_no_database_outbox_or_raw_logging_boundary() -> None:
    source = inspect.getsource(bot_reply_module).casefold()

    assert "sqlalchemy" not in source
    assert "session" not in source
    assert "outbox" not in source
    assert "raw_token" not in source
    assert "logger.exception" not in source
