from __future__ import annotations

_POSTGRES_BIGINT_MAX = 2**63 - 1


class VerifiedPrivateTelegramChatIdentity:
    __slots__ = ("_value",)

    def __init__(self, chat_id: int) -> None:
        if isinstance(chat_id, bool) or not isinstance(chat_id, int):
            raise ValueError(
                "Verified private Telegram chat identity must be numeric"
            )
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


class FakeVerifiedPrivateTelegramAdapter:
    __slots__ = ()

    def verify_private_chat(
        self,
        chat_id: int,
    ) -> VerifiedPrivateTelegramChatIdentity:
        return VerifiedPrivateTelegramChatIdentity(chat_id)
