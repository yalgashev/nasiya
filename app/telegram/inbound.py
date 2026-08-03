from __future__ import annotations

_POSTGRES_BIGINT_MAX = 2**63 - 1
TELEGRAM_CONTACT_PHONE_MAX_LENGTH = 64


class VerifiedPrivateTelegramChatIdentity:
    __slots__ = ("_value",)

    def __init__(self, chat_id: int) -> None:
        if isinstance(chat_id, bool) or not isinstance(chat_id, int):
            raise ValueError("Verified private Telegram chat identity must be numeric")
        if chat_id < 1 or chat_id > _POSTGRES_BIGINT_MAX:
            raise ValueError(
                "Verified private Telegram chat identity must be a valid "
                "positive BIGINT value"
            )

        self._value = chat_id

    def as_bigint(self) -> int:
        return self._value

    def __repr__(self) -> str:
        return "VerifiedPrivateTelegramChatIdentity(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-telegram-chat-identity>"


class TelegramUserIdentity:
    __slots__ = ("_value",)

    def __init__(self, user_id: int) -> None:
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise ValueError("Telegram user identity must be numeric")
        if user_id < 1 or user_id > _POSTGRES_BIGINT_MAX:
            raise ValueError("Telegram user identity must be a valid positive BIGINT")

        self._value = user_id

    def as_bigint(self) -> int:
        return self._value

    def __repr__(self) -> str:
        return "TelegramUserIdentity(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-telegram-user-identity>"


class SensitiveTelegramContactPhone:
    __slots__ = ("_value",)

    def __init__(self, phone: str) -> None:
        if (
            not isinstance(phone, str)
            or not phone.strip()
            or len(phone) > TELEGRAM_CONTACT_PHONE_MAX_LENGTH
        ):
            raise ValueError("Telegram contact phone must be a bounded string")
        self._value = phone

    def as_normalization_input(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SensitiveTelegramContactPhone(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-telegram-contact-phone>"


class FakeVerifiedPrivateTelegramAdapter:
    __slots__ = ()

    def verify_private_chat(
        self,
        chat_id: int,
    ) -> VerifiedPrivateTelegramChatIdentity:
        return VerifiedPrivateTelegramChatIdentity(chat_id)
