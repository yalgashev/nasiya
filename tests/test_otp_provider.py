import asyncio
import inspect
from collections.abc import Awaitable

import pytest

import app.otp.provider as provider_module
from app.otp.code import OtpCode
from app.otp.contracts import OtpDeliveryFailureCode
from app.otp.provider import (
    OtpDeliverySendStatus,
    TelegramOtpProvider,
    TelegramOtpTarget,
)
from app.telegram.bot_api import TelegramApiError, TelegramApiErrorCode
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity


class FakeBotApiClient:
    def __init__(self, error: TelegramApiError | None = None) -> None:
        self.error = error
        self.calls = []

    async def send_message(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def target() -> TelegramOtpTarget:
    return TelegramOtpTarget(
        chat_identity=VerifiedPrivateTelegramChatIdentity(998_800_100_200)
    )


def test_telegram_otp_provider_formats_message_and_sends_once() -> None:
    client = FakeBotApiClient()
    provider = TelegramOtpProvider(
        bot_api_client=client,  # type: ignore[arg-type]
        send_timeout_seconds=5,
    )

    result = run(
        provider.send_otp(
            target=target(),
            code=OtpCode("004271"),
            locale="uz-Latn",
            ttl_seconds=180,
        )
    )

    assert result.status is OtpDeliverySendStatus.SENT
    assert result.failure_code is None
    assert len(client.calls) == 1
    assert client.calls[0]["timeout_seconds"] == 5
    assert client.calls[0]["chat_id"].as_bigint() == 998_800_100_200
    assert "004271" in client.calls[0]["text"]
    assert "phone" not in client.calls[0]["text"].casefold()
    assert "parse_mode" not in client.calls[0]


@pytest.mark.parametrize(
    ("api_code", "expected_status", "expected_failure"),
    [
        (
            TelegramApiErrorCode.TRANSIENT_NETWORK,
            OtpDeliverySendStatus.UNKNOWN,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_NETWORK,
        ),
        (
            TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
            OtpDeliverySendStatus.FAILED,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_RATE_LIMIT,
        ),
        (
            TelegramApiErrorCode.TRANSIENT_SERVER,
            OtpDeliverySendStatus.FAILED,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER,
        ),
        (
            TelegramApiErrorCode.FATAL_CREDENTIAL,
            OtpDeliverySendStatus.FAILED,
            OtpDeliveryFailureCode.TELEGRAM_FATAL_CREDENTIAL,
        ),
        (
            TelegramApiErrorCode.PROTOCOL,
            OtpDeliverySendStatus.FAILED,
            OtpDeliveryFailureCode.TELEGRAM_PROTOCOL,
        ),
    ],
)
def test_telegram_otp_provider_maps_api_failures_without_retry(
    api_code: TelegramApiErrorCode,
    expected_status: OtpDeliverySendStatus,
    expected_failure: OtpDeliveryFailureCode,
) -> None:
    client = FakeBotApiClient(TelegramApiError(api_code))
    provider = TelegramOtpProvider(
        bot_api_client=client,  # type: ignore[arg-type]
        send_timeout_seconds=5,
    )

    result = run(
        provider.send_otp(
            target=target(),
            code=OtpCode("111222"),
            locale="ru",
            ttl_seconds=120,
        )
    )

    assert result.status is expected_status
    assert result.failure_code is expected_failure
    assert len(client.calls) == 1


def test_provider_boundary_is_telegram_only_and_redacted() -> None:
    source = inspect.getsource(provider_module).casefold()
    rendered_target = repr(target())
    rendered_result = repr(
        provider_module.OtpDeliverySendResult(
            status=OtpDeliverySendStatus.FAILED,
            failure_code=OtpDeliveryFailureCode.TELEGRAM_UNKNOWN,
        )
    )

    assert "sqlalchemy" not in source
    assert "session" not in source
    assert "user.phone" not in source
    assert "smsotpprovider" not in source
    assert "providerregistry" not in source
    assert "998800100200" not in rendered_target
    assert "111222" not in rendered_result
    assert "TELEGRAM_UNKNOWN" in rendered_result
