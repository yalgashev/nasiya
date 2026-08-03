import logging
from dataclasses import is_dataclass
from pathlib import Path

import pytest

import app.telegram.bot as bot_module
import app.telegram.client_ip as client_ip_module
import app.telegram.inbound as inbound_module
import app.telegram.token as token_module
from app.telegram.bot import TelegramBotUsername
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.token import (
    RawTelegramLinkToken,
    TelegramBotUsernameNotConfigured,
    TelegramContactBindingMac,
    build_telegram_start_link,
)

RAW_TOKEN = "deterministic_token-123"
RAW_IP = "203.0.113.10"
RAW_CHAT_ID = 123_456_789
RAW_TELEGRAM_USER_ID = 987_654_321
RAW_CONTACT_PHONE = "+998 90 456 78 90"
RAW_BINDING_MAC = "a" * 64
CANONICAL_BOT_USERNAME = "nasiya_linkbot"
FULL_START_LINK = f"https://t.me/{CANONICAL_BOT_USERNAME}?start={RAW_TOKEN}"


@pytest.mark.parametrize(
    ("value_object", "raw_secret"),
    [
        (RawTelegramLinkToken(RAW_TOKEN), RAW_TOKEN),
        (
            build_telegram_start_link(
                TelegramBotUsername("Nasiya_LinkBot"),
                RawTelegramLinkToken(RAW_TOKEN),
            ),
            FULL_START_LINK,
        ),
        (ResolvedClientIp(RAW_IP), RAW_IP),
        (
            VerifiedPrivateTelegramChatIdentity(RAW_CHAT_ID),
            str(RAW_CHAT_ID),
        ),
        (TelegramUserIdentity(RAW_TELEGRAM_USER_ID), str(RAW_TELEGRAM_USER_ID)),
        (SensitiveTelegramContactPhone(RAW_CONTACT_PHONE), RAW_CONTACT_PHONE),
        (TelegramContactBindingMac(RAW_BINDING_MAC), RAW_BINDING_MAC),
    ],
)
def test_sensitive_value_objects_repr_and_str_are_redacted(
    value_object: object,
    raw_secret: str,
) -> None:
    assert raw_secret not in repr(value_object)
    assert raw_secret not in str(value_object)
    assert "redacted" in repr(value_object)
    assert "redacted" in str(value_object)


def test_telegram_start_link_repr_and_str_do_not_reveal_raw_token() -> None:
    start_link = build_telegram_start_link(
        TelegramBotUsername("Nasiya_LinkBot"),
        RawTelegramLinkToken(RAW_TOKEN),
    )

    assert RAW_TOKEN not in repr(start_link)
    assert RAW_TOKEN not in str(start_link)


@pytest.mark.parametrize(
    ("constructor", "raw_sensitive_input", "expected_message"),
    [
        (
            RawTelegramLinkToken,
            "secret token with spaces",
            "Telegram link token",
        ),
        (
            TelegramBotUsername,
            "https://t.me/SensitivePlaceholderBot",
            "Telegram bot username",
        ),
        (
            ResolvedClientIp,
            "203.0.113.10, 198.51.100.20",
            "Resolved client IP",
        ),
        (
            VerifiedPrivateTelegramChatIdentity,
            -100_123_456_789,
            "Verified private Telegram chat identity",
        ),
        (
            TelegramUserIdentity,
            -987_654_321,
            "Telegram user identity",
        ),
        (
            SensitiveTelegramContactPhone,
            "x" * 65,
            "Telegram contact phone",
        ),
        (
            TelegramContactBindingMac,
            "A" * 64,
            "Telegram contact binding MAC",
        ),
    ],
)
def test_validation_exceptions_do_not_echo_sensitive_input(
    constructor,
    raw_sensitive_input: object,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        constructor(raw_sensitive_input)

    assert str(raw_sensitive_input) not in str(exc_info.value)
    assert expected_message in str(exc_info.value)


def test_start_link_not_configured_exception_does_not_reveal_raw_token() -> None:
    with pytest.raises(TelegramBotUsernameNotConfigured) as exc_info:
        build_telegram_start_link(None, RawTelegramLinkToken(RAW_TOKEN))

    assert RAW_TOKEN not in str(exc_info.value)
    assert "not configured" in str(exc_info.value)


@pytest.mark.parametrize(
    ("value_object", "raw_secret"),
    [
        (RawTelegramLinkToken(RAW_TOKEN), RAW_TOKEN),
        (
            build_telegram_start_link(
                TelegramBotUsername("Nasiya_LinkBot"),
                RawTelegramLinkToken(RAW_TOKEN),
            ),
            FULL_START_LINK,
        ),
        (ResolvedClientIp(RAW_IP), RAW_IP),
        (
            VerifiedPrivateTelegramChatIdentity(RAW_CHAT_ID),
            str(RAW_CHAT_ID),
        ),
        (TelegramUserIdentity(RAW_TELEGRAM_USER_ID), str(RAW_TELEGRAM_USER_ID)),
        (SensitiveTelegramContactPhone(RAW_CONTACT_PHONE), RAW_CONTACT_PHONE),
        (TelegramContactBindingMac(RAW_BINDING_MAC), RAW_BINDING_MAC),
    ],
)
def test_logging_objects_does_not_reveal_sensitive_values(
    caplog,
    value_object: object,
    raw_secret: str,
) -> None:
    logger = logging.getLogger("tests.telegram_sensitive_value_objects")

    with caplog.at_level(logging.INFO):
        logger.info("value via percent-s %s", value_object)
        logger.info("value via percent-r %r", value_object)
        logger.info("value via f-string %s", f"{value_object}")

    assert raw_secret not in caplog.text
    assert "redacted" in caplog.text


def test_logging_start_link_object_does_not_reveal_raw_token(caplog) -> None:
    start_link = build_telegram_start_link(
        TelegramBotUsername("Nasiya_LinkBot"),
        RawTelegramLinkToken(RAW_TOKEN),
    )
    logger = logging.getLogger("tests.telegram_sensitive_value_objects")

    with caplog.at_level(logging.INFO):
        logger.info("start link %s %r %s", start_link, start_link, f"{start_link}")

    assert RAW_TOKEN not in caplog.text
    assert FULL_START_LINK not in caplog.text


@pytest.mark.parametrize(
    "value_object",
    [
        RawTelegramLinkToken(RAW_TOKEN),
        build_telegram_start_link(
            TelegramBotUsername("Nasiya_LinkBot"),
            RawTelegramLinkToken(RAW_TOKEN),
        ),
        ResolvedClientIp(RAW_IP),
        VerifiedPrivateTelegramChatIdentity(RAW_CHAT_ID),
        TelegramUserIdentity(RAW_TELEGRAM_USER_ID),
        SensitiveTelegramContactPhone(RAW_CONTACT_PHONE),
        TelegramContactBindingMac(RAW_BINDING_MAC),
        TelegramBotUsername("Nasiya_LinkBot"),
    ],
)
def test_sensitive_value_objects_have_no_public_serialize_all_api(
    value_object: object,
) -> None:
    assert not is_dataclass(value_object)
    assert not hasattr(value_object, "__dict__")
    assert not hasattr(value_object, "dict")
    assert not hasattr(value_object, "model_dump")
    assert not hasattr(value_object, "json")


def test_explicit_reveal_methods_are_narrowly_named() -> None:
    raw_token = RawTelegramLinkToken(RAW_TOKEN)
    start_link = build_telegram_start_link(
        TelegramBotUsername("Nasiya_LinkBot"),
        raw_token,
    )
    resolved_ip = ResolvedClientIp(RAW_IP)
    verified_chat_identity = VerifiedPrivateTelegramChatIdentity(RAW_CHAT_ID)
    telegram_user_identity = TelegramUserIdentity(RAW_TELEGRAM_USER_ID)
    contact_phone = SensitiveTelegramContactPhone(RAW_CONTACT_PHONE)
    binding_mac = TelegramContactBindingMac(RAW_BINDING_MAC)
    bot_username = TelegramBotUsername("Nasiya_LinkBot")

    assert raw_token.as_internal_value() == RAW_TOKEN
    assert start_link.as_delivery_url() == FULL_START_LINK
    assert resolved_ip.as_hmac_input() == RAW_IP
    assert verified_chat_identity.as_bigint() == RAW_CHAT_ID
    assert telegram_user_identity.as_bigint() == RAW_TELEGRAM_USER_ID
    assert contact_phone.as_normalization_input() == RAW_CONTACT_PHONE
    assert binding_mac.as_stored_value() == RAW_BINDING_MAC
    assert bot_username.as_username() == CANONICAL_BOT_USERNAME

    for value_object in (
        raw_token,
        start_link,
        resolved_ip,
        verified_chat_identity,
        telegram_user_identity,
        contact_phone,
        binding_mac,
        bot_username,
    ):
        assert not hasattr(value_object, "value")
        assert not hasattr(value_object, "secret")
        assert not hasattr(value_object, "raw")
        assert not hasattr(value_object, "url")


def test_explicit_reveal_methods_are_not_logged_in_telegram_modules() -> None:
    module_sources = (
        Path(token_module.__file__).read_text(encoding="utf-8"),
        Path(client_ip_module.__file__).read_text(encoding="utf-8"),
        Path(bot_module.__file__).read_text(encoding="utf-8"),
        Path(inbound_module.__file__).read_text(encoding="utf-8"),
    )

    for source in module_sources:
        assert "logging" not in source
        assert "logger" not in source
        assert "print(" not in source


def test_sensitive_wrappers_are_not_used_in_templates() -> None:
    templates_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in templates_dir.rglob("*")
        if path.is_file()
    )

    forbidden_template_terms = {
        "RawTelegramLinkToken",
        "TelegramStartLink",
        "ResolvedClientIp",
        "VerifiedPrivateTelegramChatIdentity",
        "TelegramUserIdentity",
        "SensitiveTelegramContactPhone",
        "TelegramContactBindingMac",
        "as_internal_value",
        "as_delivery_url",
        "as_hmac_input",
        "as_bigint",
        "as_normalization_input",
        "as_stored_value",
    }

    for forbidden_template_term in forbidden_template_terms:
        assert forbidden_template_term not in template_text
