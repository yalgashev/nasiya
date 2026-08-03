from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from typing import Final

from pydantic import SecretStr

from app.telegram.bot import TelegramBotUsername
from app.telegram.inbound import (
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)

TELEGRAM_LINK_TOKEN_ENTROPY_BYTES: Final = 32
_URLSAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_TELEGRAM_START_LINK_BASE_URL: Final = "https://t.me"
_CONTACT_BINDING_DOMAIN: Final = b"NASIYA-TELEGRAM-CONTACT-BINDING-V1\0"


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


class TelegramContactBindingMac:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("Telegram contact binding MAC must be a string")
        if _SHA256_HEX_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "Telegram contact binding MAC must be lowercase SHA-256 hex"
            )
        self._value = value

    def as_stored_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "TelegramContactBindingMac(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-telegram-contact-binding-mac>"


def create_telegram_link_token(
    token_generator: Callable[[int], str] | None = None,
) -> RawTelegramLinkToken:
    resolved_generator = token_generator or secrets.token_urlsafe
    return RawTelegramLinkToken(resolved_generator(TELEGRAM_LINK_TOKEN_ENTROPY_BYTES))


def hash_telegram_link_token(raw_token: RawTelegramLinkToken) -> str:
    return hashlib.sha256(raw_token.as_internal_value().encode("utf-8")).hexdigest()


def derive_telegram_contact_binding_mac(
    *,
    rate_limit_hmac_key: SecretStr,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
) -> TelegramContactBindingMac:
    if not isinstance(rate_limit_hmac_key, SecretStr):
        raise ValueError("Telegram contact binding HMAC key is not configured")
    secret = rate_limit_hmac_key.get_secret_value()
    if not secret:
        raise ValueError("Telegram contact binding HMAC key is not configured")
    if not isinstance(chat_identity, VerifiedPrivateTelegramChatIdentity):
        raise ValueError("Telegram contact binding chat identity is invalid")
    if not isinstance(sender_identity, TelegramUserIdentity):
        raise ValueError("Telegram contact binding sender identity is invalid")
    canonical_input = (
        _CONTACT_BINDING_DOMAIN
        + str(chat_identity.as_bigint()).encode("ascii")
        + b"\0"
        + str(sender_identity.as_bigint()).encode("ascii")
    )
    return TelegramContactBindingMac(
        hmac.new(
            secret.encode("utf-8"),
            canonical_input,
            hashlib.sha256,
        ).hexdigest()
    )


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
