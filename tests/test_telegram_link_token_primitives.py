import inspect
import logging
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

import app.telegram.token as telegram_token_module
from app.telegram.bot import TelegramBotUsername
from app.telegram.models import TelegramLinkToken
from app.telegram.token import (
    TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
    RawTelegramLinkToken,
    TelegramBotUsernameNotConfigured,
    TelegramStartLink,
    build_telegram_start_link,
    create_telegram_link_token,
    hash_telegram_link_token,
)

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
URLSAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
DETERMINISTIC_RAW_TOKEN = "deterministic_token-123"
DETERMINISTIC_TOKEN_SHA256 = (
    "7c5b13b59a6377db251ee7b60ec5960231933359b59a58461b700669c416a7c3"
)


def test_telegram_link_tokens_do_not_repeat() -> None:
    token_values = {
        create_telegram_link_token().as_internal_value() for _ in range(128)
    }

    assert len(token_values) == 128


def test_telegram_link_token_has_at_least_32_bytes_of_entropy() -> None:
    token = create_telegram_link_token()

    assert TELEGRAM_LINK_TOKEN_ENTROPY_BYTES >= 32
    assert isinstance(token, RawTelegramLinkToken)
    assert len(token.as_internal_value()) >= 43


def test_production_generator_uses_at_least_32_byte_entropy_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_token_urlsafe(entropy_bytes: int) -> str:
        calls.append(entropy_bytes)
        return DETERMINISTIC_RAW_TOKEN

    monkeypatch.setattr(
        telegram_token_module.secrets,
        "token_urlsafe",
        fake_token_urlsafe,
    )

    token = create_telegram_link_token()

    assert calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert calls[0] >= 32
    assert token.as_internal_value() == DETERMINISTIC_RAW_TOKEN


def test_telegram_link_token_uses_url_safe_canonical_representation() -> None:
    token_value = create_telegram_link_token().as_internal_value()

    assert URLSAFE_TOKEN_PATTERN.fullmatch(token_value)
    assert "=" not in token_value
    assert token_value == token_value.strip()


def test_telegram_link_token_generator_can_be_injected() -> None:
    calls: list[int] = []

    def fake_generator(entropy_bytes: int) -> str:
        calls.append(entropy_bytes)
        return DETERMINISTIC_RAW_TOKEN

    token = create_telegram_link_token(token_generator=fake_generator)

    assert calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert token.as_internal_value() == DETERMINISTIC_RAW_TOKEN


def test_raw_telegram_link_token_rejects_empty_or_non_url_safe_values() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        RawTelegramLinkToken("")

    with pytest.raises(ValueError, match="URL-safe"):
        RawTelegramLinkToken("not url safe")


def test_validation_error_does_not_reveal_raw_token() -> None:
    invalid_raw_token = "secret token with spaces"

    with pytest.raises(ValueError) as exc_info:
        RawTelegramLinkToken(invalid_raw_token)

    assert invalid_raw_token not in str(exc_info.value)
    assert "URL-safe" in str(exc_info.value)


def test_telegram_link_token_hash_is_64_lowercase_hex_characters() -> None:
    token_hash = hash_telegram_link_token(create_telegram_link_token())

    assert SHA256_HEX_PATTERN.fullmatch(token_hash)


def test_telegram_link_token_hash_matches_exact_sha256() -> None:
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN))

    assert token_hash == DETERMINISTIC_TOKEN_SHA256
    assert SHA256_HEX_PATTERN.fullmatch(token_hash)


def test_same_telegram_link_token_produces_same_hash() -> None:
    token = create_telegram_link_token()

    assert hash_telegram_link_token(token) == hash_telegram_link_token(token)


def test_different_telegram_link_tokens_produce_different_hashes() -> None:
    first_hash = hash_telegram_link_token(RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN))
    second_hash = hash_telegram_link_token(
        RawTelegramLinkToken("another_deterministic-token_456")
    )

    assert first_hash != second_hash


def test_telegram_link_token_hash_does_not_reveal_raw_token() -> None:
    token = create_telegram_link_token()
    raw_token = token.as_internal_value()
    token_hash = hash_telegram_link_token(token)

    assert token_hash != raw_token
    assert raw_token not in token_hash


def test_raw_telegram_link_token_repr_str_and_logging_are_redacted(caplog) -> None:
    token = create_telegram_link_token()
    raw_token = token.as_internal_value()
    logger = logging.getLogger("tests.telegram_link_token_primitives")

    with caplog.at_level(logging.INFO):
        logger.info("created token %s %r", token, token)

    assert raw_token not in str(token)
    assert raw_token not in repr(token)
    assert raw_token not in caplog.text
    assert "redacted" in caplog.text


def test_url_safe_token_is_valid_deep_link_payload() -> None:
    token = create_telegram_link_token(token_generator=lambda _: "abc_DEF-123")
    token_value = token.as_internal_value()
    query_string = urlencode({"start": token_value})
    parsed_query = parse_qs(urlsplit(f"https://t.me/nasiya_bot?{query_string}").query)

    assert URLSAFE_TOKEN_PATTERN.fullmatch(token_value)
    assert parsed_query == {"start": [token_value]}
    assert query_string == f"start={token_value}"


def test_telegram_start_link_builds_canonical_lowercase_url() -> None:
    raw_token = RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN)
    bot_username = TelegramBotUsername("Nasiya_LinkBot")

    start_link = build_telegram_start_link(bot_username, raw_token)

    assert isinstance(start_link, TelegramStartLink)
    assert start_link.as_delivery_url() == (
        f"https://t.me/nasiya_linkbot?start={DETERMINISTIC_RAW_TOKEN}"
    )


def test_telegram_start_link_requires_configured_bot_username() -> None:
    raw_token = RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN)

    with pytest.raises(TelegramBotUsernameNotConfigured) as exc_info:
        build_telegram_start_link(None, raw_token)

    assert DETERMINISTIC_RAW_TOKEN not in str(exc_info.value)
    assert "not configured" in str(exc_info.value)


def test_telegram_start_link_does_not_use_environment_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_token = RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "Nasiya_LinkBot")

    with pytest.raises(TelegramBotUsernameNotConfigured):
        build_telegram_start_link(None, raw_token)


def test_telegram_start_link_repr_str_and_logging_are_redacted(caplog) -> None:
    raw_token = RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN)
    start_link = build_telegram_start_link(
        TelegramBotUsername("Nasiya_LinkBot"),
        raw_token,
    )
    delivery_url = start_link.as_delivery_url()
    logger = logging.getLogger("tests.telegram_start_link")

    with caplog.at_level(logging.INFO):
        logger.info("created start link %s %r", start_link, start_link)

    assert DETERMINISTIC_RAW_TOKEN not in str(start_link)
    assert DETERMINISTIC_RAW_TOKEN not in repr(start_link)
    assert DETERMINISTIC_RAW_TOKEN not in caplog.text
    assert delivery_url not in str(start_link)
    assert delivery_url not in repr(start_link)
    assert delivery_url not in caplog.text
    assert "redacted" in caplog.text


def test_start_link_full_url_requires_explicit_delivery_method() -> None:
    start_link = build_telegram_start_link(
        TelegramBotUsername("Nasiya_LinkBot"),
        RawTelegramLinkToken(DETERMINISTIC_RAW_TOKEN),
    )

    assert hasattr(start_link, "as_delivery_url")
    assert not hasattr(start_link, "url")
    assert not hasattr(start_link, "href")


def test_raw_value_has_only_narrow_internal_accessor() -> None:
    token = create_telegram_link_token()

    assert hasattr(token, "as_internal_value")
    assert not hasattr(token, "as_cookie_value")
    assert not hasattr(token, "value")


def test_telegram_link_token_db_model_has_no_raw_token_column() -> None:
    forbidden_columns = {
        "token",
        "raw_token",
        "link_token",
        "raw_link_token",
        "deep_link",
        "token_ciphertext",
    }

    assert forbidden_columns.isdisjoint(TelegramLinkToken.__table__.columns.keys())


def test_token_primitive_does_not_import_network_or_db_modules() -> None:
    source = inspect.getsource(telegram_token_module)
    forbidden_source_terms = {
        "sqlalchemy",
        "psycopg",
        "create_engine",
        "Session",
        "requests",
        "httpx",
        "urllib.request",
        "socket",
        "aiogram",
        "telebot",
    }

    for forbidden_source_term in forbidden_source_terms:
        assert forbidden_source_term not in source


def test_start_link_builder_is_pure_and_does_not_issue_or_persist_tokens() -> None:
    source = inspect.getsource(build_telegram_start_link)
    forbidden_source_terms = {
        "os.environ",
        "getenv",
        "Settings",
        "create_telegram_link_token",
        "hash_telegram_link_token",
        "TelegramLinkToken(",
        "from app.telegram.models",
        "sqlalchemy",
        "psycopg",
        "requests",
        "httpx",
        "socket",
    }

    for forbidden_source_term in forbidden_source_terms:
        assert forbidden_source_term not in source
