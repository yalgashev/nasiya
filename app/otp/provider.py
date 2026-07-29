from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.otp.code import OtpCode
from app.otp.contracts import OtpDeliveryFailureCode
from app.otp.message import format_login_otp_message
from app.telegram.bot_api import (
    TelegramApiError,
    TelegramApiErrorCode,
    TelegramBotApiClient,
)
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity


class OtpDeliverySendStatus(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, repr=False)
class TelegramOtpTarget:
    chat_identity: VerifiedPrivateTelegramChatIdentity

    def __repr__(self) -> str:
        return "TelegramOtpTarget(chat_identity=<redacted>)"


@dataclass(frozen=True, repr=False)
class OtpDeliverySendResult:
    status: OtpDeliverySendStatus
    failure_code: OtpDeliveryFailureCode | str | None = None

    def __repr__(self) -> str:
        return (
            "OtpDeliverySendResult("
            f"status={self.status.value}, failure_code={self.failure_code})"
        )


class OtpDeliveryProvider(Protocol):
    async def send_otp(
        self,
        *,
        target: TelegramOtpTarget,
        code: OtpCode,
        locale: str,
        ttl_seconds: int,
    ) -> OtpDeliverySendResult:
        pass


@dataclass(frozen=True)
class TelegramOtpProvider:
    bot_api_client: TelegramBotApiClient
    send_timeout_seconds: int

    async def send_otp(
        self,
        *,
        target: TelegramOtpTarget,
        code: OtpCode,
        locale: str,
        ttl_seconds: int,
    ) -> OtpDeliverySendResult:
        if self.send_timeout_seconds < 1:
            raise ValueError("OTP send timeout must be positive")
        message = format_login_otp_message(
            code=code,
            ttl_seconds=ttl_seconds,
            locale=locale,
        )
        try:
            await self.bot_api_client.send_message(
                chat_id=target.chat_identity,
                text=message,
                timeout_seconds=self.send_timeout_seconds,
            )
        except TelegramApiError as exc:
            return _map_telegram_api_error(exc)
        return OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT)


def _map_telegram_api_error(error: TelegramApiError) -> OtpDeliverySendResult:
    if error.code is TelegramApiErrorCode.TRANSIENT_NETWORK:
        return OtpDeliverySendResult(
            status=OtpDeliverySendStatus.UNKNOWN,
            failure_code=OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_NETWORK,
        )
    if error.code is TelegramApiErrorCode.TRANSIENT_RATE_LIMIT:
        return OtpDeliverySendResult(
            status=OtpDeliverySendStatus.FAILED,
            failure_code=OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_RATE_LIMIT,
        )
    if error.code is TelegramApiErrorCode.TRANSIENT_SERVER:
        return OtpDeliverySendResult(
            status=OtpDeliverySendStatus.FAILED,
            failure_code=OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER,
        )
    if error.code is TelegramApiErrorCode.FATAL_CREDENTIAL:
        return OtpDeliverySendResult(
            status=OtpDeliverySendStatus.FAILED,
            failure_code=OtpDeliveryFailureCode.TELEGRAM_FATAL_CREDENTIAL,
        )
    if error.code is TelegramApiErrorCode.PROTOCOL:
        return OtpDeliverySendResult(
            status=OtpDeliverySendStatus.FAILED,
            failure_code=OtpDeliveryFailureCode.TELEGRAM_PROTOCOL,
        )
    return OtpDeliverySendResult(
        status=OtpDeliverySendStatus.FAILED,
        failure_code=OtpDeliveryFailureCode.TELEGRAM_UNKNOWN,
    )
