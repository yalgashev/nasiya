from __future__ import annotations

import re

_BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class TelegramBotUsername:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("Telegram bot username must be a string")
        raw_value = value
        if not raw_value:
            raise ValueError("Telegram bot username cannot be empty")
        if raw_value != raw_value.strip() or any(
            character.isspace() for character in raw_value
        ):
            raise ValueError("Telegram bot username must not contain whitespace")
        if raw_value.startswith("@"):
            raise ValueError("Telegram bot username must not include @")
        if "://" in raw_value or "/" in raw_value:
            raise ValueError("Telegram bot username must be a username, not a URL")
        if _BOT_USERNAME_PATTERN.fullmatch(raw_value) is None:
            raise ValueError(
                "Telegram bot username must be 5-32 ASCII letters, "
                "digits, or underscores"
            )
        if not raw_value.casefold().endswith("bot"):
            raise ValueError("Telegram bot username must end with bot")

        self._value = raw_value.lower()

    def as_username(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"TelegramBotUsername({self._value!r})"

    def __str__(self) -> str:
        return self._value
