import pytest

from app.telegram.bot_api import (
    TelegramMessageEnvelope,
    TelegramUpdateEnvelope,
)
from app.telegram.update_parser import (
    TELEGRAM_START_TOKEN_MAX_LENGTH,
    TelegramUpdateParseCode,
    parse_telegram_update,
)


def update_with_message(
    *,
    chat_id: int = 12345,
    chat_type: str = "private",
    text: str | None = "/start safe_token-123",
    structurally_valid: bool = True,
    language_code: str | None = None,
) -> TelegramUpdateEnvelope:
    return TelegramUpdateEnvelope(
        update_id=7,
        message=TelegramMessageEnvelope(
            chat_id=chat_id,
            chat_type=chat_type,
            text=text,
            structurally_valid=structurally_valid,
            language_code=language_code,
        ),
    )


def test_private_start_parser_returns_only_verified_identity_and_redacted_token() -> (
    None
):
    parsed = parse_telegram_update(update_with_message())

    assert parsed.code is TelegramUpdateParseCode.PRIVATE_START
    assert parsed.chat_identity is not None
    assert parsed.chat_identity.as_bigint() == 12345
    assert parsed.raw_token is not None
    assert parsed.raw_token.as_internal_value() == "safe_token-123"
    assert "12345" not in repr(parsed)
    assert "safe_token-123" not in repr(parsed)


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (
            TelegramUpdateEnvelope(update_id=1),
            TelegramUpdateParseCode.UNSUPPORTED_UPDATE,
        ),
        (
            update_with_message(chat_type="group"),
            TelegramUpdateParseCode.NON_PRIVATE_CHAT,
        ),
        (
            update_with_message(chat_type="channel"),
            TelegramUpdateParseCode.NON_PRIVATE_CHAT,
        ),
        (
            update_with_message(structurally_valid=False),
            TelegramUpdateParseCode.MALFORMED_START,
        ),
        (
            update_with_message(chat_id=0),
            TelegramUpdateParseCode.MALFORMED_START,
        ),
        (
            update_with_message(text=None),
            TelegramUpdateParseCode.MALFORMED_START,
        ),
    ],
)
def test_unsupported_non_private_and_malformed_updates_are_safe_terminal(
    update: TelegramUpdateEnvelope,
    expected: TelegramUpdateParseCode,
) -> None:
    parsed = parse_telegram_update(update)

    assert parsed.code is expected
    assert parsed.chat_identity is None
    assert parsed.raw_token is None


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "/start ",
        "/start  token",
        " /start token",
        "/start token ",
        "/START token",
        "/start@Nasiya_LinkBot token",
        "/start token with spaces",
        "/start token!",
        f"/start {'a' * (TELEGRAM_START_TOKEN_MAX_LENGTH + 1)}",
    ],
)
def test_start_grammar_is_exact_bounded_and_url_safe(text: str) -> None:
    parsed = parse_telegram_update(update_with_message(text=text))

    assert parsed.code is TelegramUpdateParseCode.MALFORMED_START
    assert parsed.chat_identity is None
    assert parsed.raw_token is None


def test_parser_accepts_maximum_bounded_token() -> None:
    token = "a" * TELEGRAM_START_TOKEN_MAX_LENGTH
    parsed = parse_telegram_update(update_with_message(text=f"/start {token}"))

    assert parsed.code is TelegramUpdateParseCode.PRIVATE_START
    assert parsed.raw_token is not None
    assert parsed.raw_token.as_internal_value() == token


def test_parser_carries_optional_language_only_in_redacted_memory_value() -> None:
    parsed = parse_telegram_update(update_with_message(language_code="ru-RU"))

    assert parsed.language_code == "ru-RU"
    assert "ru-RU" not in repr(parsed)


def test_envelope_repr_redacts_transport_message_and_chat() -> None:
    update = update_with_message(text="/start highly_sensitive_token")

    rendered = f"{update!r} {update.message!r}"
    assert "highly_sensitive_token" not in rendered
    assert "12345" not in rendered
