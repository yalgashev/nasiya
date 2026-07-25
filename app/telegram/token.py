from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from typing import Final

from app.telegram.bot import TelegramBotUsername

TELEGRAM_LINK_TOKEN_ENTROPY_BYTES: Final = 32
_URLSAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_TELEGRAM_START_LINK_BASE_URL: Final = "https://t.me"


class TelegramBotUsernameNotConfigured(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Telegram bot username is not configured")


class RawTelegramLinkToken:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("Telegram link token must be a string")
        if not value:
            raise ValueError("Telegram link token cannot be empty")
        if _URLSAFE_TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("Telegram link token must be URL-safe")
        self._value = value

    def as_internal_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "RawTelegramLinkToken(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-telegram-link-token>"


class TelegramStartLink:
    __slots__ = ("_url",)

    def __init__(self, url: str) -> None:
        if not isinstance(url, str) or not url:
            raise ValueError("Telegram start link URL is required")
        self._url = url

    def as_delivery_url(self) -> str:
        return self._url

    def __repr__(self) -> str:
        return "TelegramStartLink(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-telegram-start-link>"


def create_telegram_link_token(
    token_generator: Callable[[int], str] | None = None,
) -> RawTelegramLinkToken:
    resolved_generator = token_generator or secrets.token_urlsafe
    return RawTelegramLinkToken(
        resolved_generator(TELEGRAM_LINK_TOKEN_ENTROPY_BYTES)
    )


def hash_telegram_link_token(raw_token: RawTelegramLinkToken) -> str:
    return hashlib.sha256(
        raw_token.as_internal_value().encode("utf-8")
    ).hexdigest()


def build_telegram_start_link(
    bot_username: TelegramBotUsername | None,
    raw_token: RawTelegramLinkToken,
) -> TelegramStartLink:
    if bot_username is None:
        raise TelegramBotUsernameNotConfigured()

    return TelegramStartLink(
        f"{_TELEGRAM_START_LINK_BASE_URL}/"
        f"{bot_username.as_username()}?start={raw_token.as_internal_value()}"
    )
