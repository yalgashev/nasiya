from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.telegram.bot_api import TelegramUpdateEnvelope
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.token import RawTelegramLinkToken

TELEGRAM_START_TOKEN_MAX_LENGTH: Final = 64


class TelegramUpdateParseCode(StrEnum):
    PRIVATE_START = "PRIVATE_START"
    PRIVATE_CONTACT = "PRIVATE_CONTACT"
    UNSUPPORTED_UPDATE = "UNSUPPORTED_UPDATE"
    NON_PRIVATE_CHAT = "NON_PRIVATE_CHAT"
    MALFORMED_START = "MALFORMED_START"
    MALFORMED_CONTACT = "MALFORMED_CONTACT"


@dataclass(frozen=True, repr=False)
class ParsedTelegramUpdate:
    code: TelegramUpdateParseCode
    chat_identity: VerifiedPrivateTelegramChatIdentity | None = None
    sender_identity: TelegramUserIdentity | None = None
    contact_identity: TelegramUserIdentity | None = None
    contact_phone: SensitiveTelegramContactPhone | None = None
    raw_token: RawTelegramLinkToken | None = None
    language_code: str | None = None

    def __repr__(self) -> str:
        return (
            "ParsedTelegramUpdate("
            f"code={self.code.value!r}, chat_identity=<redacted>, "
            "sender_identity=<redacted>, contact_identity=<redacted>, "
            "contact_phone=<redacted>, raw_token=<redacted>, "
            "language_code=<redacted>)"
        )


def parse_telegram_update(
    update: TelegramUpdateEnvelope,
) -> ParsedTelegramUpdate:
    message = update.message
    if message is None:
        return ParsedTelegramUpdate(TelegramUpdateParseCode.UNSUPPORTED_UPDATE)
    if not message.structurally_valid:
        return ParsedTelegramUpdate(TelegramUpdateParseCode.MALFORMED_START)
    if message.chat_type != "private":
        return ParsedTelegramUpdate(TelegramUpdateParseCode.NON_PRIVATE_CHAT)
    if message.chat_id is None:
        return ParsedTelegramUpdate(TelegramUpdateParseCode.MALFORMED_START)
    try:
        chat_identity = VerifiedPrivateTelegramChatIdentity(message.chat_id)
    except ValueError:
        return ParsedTelegramUpdate(TelegramUpdateParseCode.MALFORMED_START)

    if message.contact_present:
        if (
            message.text is not None
            or message.is_forwarded
            or message.sender_identity is None
            or message.contact_identity is None
            or message.contact_phone is None
            or message.sender_identity.as_bigint()
            != message.contact_identity.as_bigint()
        ):
            return ParsedTelegramUpdate(
                code=TelegramUpdateParseCode.MALFORMED_CONTACT,
                chat_identity=chat_identity,
                language_code=message.language_code,
            )
        return ParsedTelegramUpdate(
            code=TelegramUpdateParseCode.PRIVATE_CONTACT,
            chat_identity=chat_identity,
            sender_identity=message.sender_identity,
            contact_identity=message.contact_identity,
            contact_phone=message.contact_phone,
            language_code=message.language_code,
        )

    text = message.text
    if (
        message.sender_identity is None
        or text is None
        or not text.startswith("/start ")
    ):
        return ParsedTelegramUpdate(TelegramUpdateParseCode.MALFORMED_START)
    token_value = text.removeprefix("/start ")
    if (
        not token_value
        or token_value != token_value.strip()
        or len(token_value) > TELEGRAM_START_TOKEN_MAX_LENGTH
        or " " in token_value
    ):
        return ParsedTelegramUpdate(TelegramUpdateParseCode.MALFORMED_START)
    try:
        raw_token = RawTelegramLinkToken(token_value)
    except ValueError:
        return ParsedTelegramUpdate(TelegramUpdateParseCode.MALFORMED_START)
    return ParsedTelegramUpdate(
        code=TelegramUpdateParseCode.PRIVATE_START,
        chat_identity=chat_identity,
        sender_identity=message.sender_identity,
        raw_token=raw_token,
        language_code=message.language_code,
    )
