import pytest

from app.telegram.bot_api import (
    TelegramMessageEnvelope,
    TelegramUpdateEnvelope,
)
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
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
    sender_user_id: int | None = 12345,
    contact_present: bool = False,
    is_forwarded: bool = False,
    contact_user_id: int | None = None,
    contact_phone: str | None = None,
) -> TelegramUpdateEnvelope:
    return TelegramUpdateEnvelope(
        update_id=7,
        message=TelegramMessageEnvelope(
            chat_id=chat_id,
            chat_type=chat_type,
            text=text,
            structurally_valid=structurally_valid,
            language_code=language_code,
            sender_identity=(
                TelegramUserIdentity(sender_user_id)
                if sender_user_id is not None
                else None
            ),
            contact_present=contact_present,
            is_forwarded=is_forwarded,
            contact_identity=(
                TelegramUserIdentity(contact_user_id)
                if contact_user_id is not None
                else None
            ),
            contact_phone=(
                SensitiveTelegramContactPhone(contact_phone)
                if contact_phone is not None
                else None
            ),
        ),
    )


def test_private_start_parser_returns_only_verified_identity_and_redacted_token() -> (
    None
):
    parsed = parse_telegram_update(update_with_message())

    assert parsed.code is TelegramUpdateParseCode.PRIVATE_START
    assert parsed.chat_identity is not None
    assert parsed.chat_identity.as_bigint() == 12345
    assert parsed.sender_identity is not None
    assert parsed.sender_identity.as_bigint() == 12345
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
            update_with_message(sender_user_id=None),
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


def test_private_self_contact_returns_only_redacted_typed_values() -> None:
    raw_phone = "+998 90 123 45 67"
    parsed = parse_telegram_update(
        update_with_message(
            text=None,
            sender_user_id=987654,
            contact_present=True,
            contact_user_id=987654,
            contact_phone=raw_phone,
        )
    )

    assert parsed.code is TelegramUpdateParseCode.PRIVATE_CONTACT
    assert parsed.chat_identity is not None
    assert parsed.sender_identity is not None
    assert parsed.contact_identity is not None
    assert parsed.contact_phone is not None
    assert parsed.sender_identity.as_bigint() == 987654
    assert parsed.contact_identity.as_bigint() == 987654
    assert parsed.contact_phone.as_normalization_input() == raw_phone
    rendered = repr(parsed)
    assert "987654" not in rendered
    assert raw_phone not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"sender_user_id": None},
        {"contact_user_id": None},
        {"contact_phone": None},
        {"sender_user_id": 111, "contact_user_id": 222},
        {"text": "/start safe_token-123"},
    ],
)
def test_contact_requires_complete_unambiguous_self_contact(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "text": None,
        "sender_user_id": 111,
        "contact_present": True,
        "contact_user_id": 111,
        "contact_phone": "+998901234567",
    }
    values.update(overrides)

    parsed = parse_telegram_update(update_with_message(**values))  # type: ignore[arg-type]

    assert parsed.code is TelegramUpdateParseCode.MALFORMED_CONTACT
    assert parsed.chat_identity is not None
    assert parsed.chat_identity.as_bigint() == 12345
    assert parsed.sender_identity is None
    assert parsed.contact_identity is None
    assert parsed.contact_phone is None
    assert parsed.raw_token is None


def test_private_malformed_contact_preserves_only_safe_reply_context() -> None:
    raw_phone = "+998901234567"
    parsed = parse_telegram_update(
        update_with_message(
            text=None,
            language_code="ru-RU",
            sender_user_id=111,
            contact_present=True,
            contact_user_id=222,
            contact_phone=raw_phone,
        )
    )

    assert parsed.code is TelegramUpdateParseCode.MALFORMED_CONTACT
    assert parsed.chat_identity is not None
    assert parsed.language_code == "ru-RU"
    assert parsed.sender_identity is None
    assert parsed.contact_identity is None
    assert parsed.contact_phone is None
    rendered = repr(parsed)
    assert raw_phone not in rendered
    assert "111" not in rendered
    assert "222" not in rendered


def test_contact_requires_present_contact_user_id() -> None:
    parsed = parse_telegram_update(
        update_with_message(
            text=None,
            sender_user_id=111,
            contact_present=True,
            contact_user_id=None,
            contact_phone="+998901234567",
        )
    )

    assert parsed.code is TelegramUpdateParseCode.MALFORMED_CONTACT
    assert parsed.contact_identity is None
    assert parsed.contact_phone is None


def test_contact_user_id_must_equal_sender_user_id() -> None:
    parsed = parse_telegram_update(
        update_with_message(
            text=None,
            sender_user_id=111,
            contact_present=True,
            contact_user_id=222,
            contact_phone="+998901234567",
        )
    )

    assert parsed.code is TelegramUpdateParseCode.MALFORMED_CONTACT
    assert parsed.sender_identity is None
    assert parsed.contact_identity is None
    assert parsed.contact_phone is None


def test_forwarded_or_other_person_contact_is_rejected() -> None:
    raw_phone = "+998901234567"
    parsed = parse_telegram_update(
        update_with_message(
            text=None,
            sender_user_id=111,
            contact_present=True,
            is_forwarded=True,
            contact_user_id=111,
            contact_phone=raw_phone,
        )
    )

    assert parsed.code is TelegramUpdateParseCode.MALFORMED_CONTACT
    assert parsed.chat_identity is not None
    assert parsed.sender_identity is None
    assert parsed.contact_identity is None
    assert parsed.contact_phone is None
    assert raw_phone not in repr(parsed)


def test_contact_requires_private_chat() -> None:
    parsed = parse_telegram_update(
        update_with_message(
            chat_type="group",
            text=None,
            sender_user_id=111,
            contact_present=True,
            contact_user_id=111,
            contact_phone="+998901234567",
        )
    )

    assert parsed.code is TelegramUpdateParseCode.NON_PRIVATE_CHAT
    assert parsed.contact_phone is None


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
