import inspect

import pytest

import app.otp.message as message_module
from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose
from app.otp.message import (
    OTP_MESSAGE_MAX_LENGTH,
    OtpTelegramMessage,
    format_login_otp_message,
    format_registration_otp_message,
    prepare_otp_telegram_message,
)


def test_login_otp_message_locales_and_fallback_are_privacy_safe() -> None:
    code = OtpCode("004271")

    uz_message = format_login_otp_message(
        code=code,
        ttl_seconds=180,
        locale="uz-Latn",
    )
    ru_message = format_login_otp_message(
        code=code,
        ttl_seconds=120,
        locale="ru",
    )
    fallback_message = format_login_otp_message(
        code=code,
        ttl_seconds=60,
        locale="en",
    )

    assert "004271" in uz_message
    assert "3 daqiqa" in uz_message
    assert "004271" in ru_message
    assert "2 min" in ru_message
    assert fallback_message != ru_message
    assert fallback_message == format_login_otp_message(
        code=code,
        ttl_seconds=60,
        locale="uz-Latn",
    )

    rendered = f"{uz_message}\n{ru_message}\n{fallback_message}".casefold()
    for forbidden in (
        "+998",
        "phone",
        "telefon",
        "name",
        "shop",
        "account",
        "link",
        "token",
        "<b>",
        "parse_mode",
    ):
        assert forbidden not in rendered
    assert max(map(len, (uz_message, ru_message, fallback_message))) <= (
        OTP_MESSAGE_MAX_LENGTH
    )


def test_login_otp_message_formatter_has_no_io_dependencies() -> None:
    source = inspect.getsource(message_module)

    assert "sqlalchemy" not in source
    assert "httpx" not in source
    assert "os.environ" not in source
    assert "Settings" not in source


def test_login_otp_message_rejects_invalid_inputs_without_raw_code_repr() -> None:
    code = OtpCode("123456")

    assert "123456" not in repr(code)
    assert "123456" not in str(code)
    try:
        format_login_otp_message(code=code, ttl_seconds=0, locale="uz-Latn")
    except ValueError as exc:
        assert "123456" not in str(exc)
    else:
        raise AssertionError("invalid OTP message TTL was accepted")


def test_registration_otp_message_locales_and_fallback_are_purpose_specific() -> None:
    code = OtpCode("004271")

    uz_message = format_registration_otp_message(
        code=code,
        ttl_seconds=180,
        locale="uz-Latn",
    )
    ru_message = format_registration_otp_message(
        code=code,
        ttl_seconds=120,
        locale="ru",
    )
    fallback_message = format_registration_otp_message(
        code=code,
        ttl_seconds=60,
        locale="unsupported",
    )

    assert uz_message == (
        "Faollashtirish kodi: 004271\n"
        "Amal qilish muddati: 3 daqiqa.\n"
        "Bu kodni hech kimga bermang.\n"
        "Agar faollashtirishni siz so'ramagan bo'lsangiz, "
        "xabarni e'tiborsiz qoldiring."
    )
    assert ru_message == (
        "Kod aktivatsii: 004271\n"
        "Srok deystviya: 2 min.\n"
        "Nikomu ne peredavayte etot kod.\n"
        "Esli vy ne zaprashivali aktivatsiyu, "
        "proignoriruyte eto soobshchenie."
    )
    assert fallback_message == format_registration_otp_message(
        code=code,
        ttl_seconds=60,
        locale="uz-Latn",
    )


def test_typed_message_wrapper_redacts_raw_code_and_rejects_purpose_fallback() -> None:
    prepared = prepare_otp_telegram_message(
        purpose=OtpPurpose.REGISTRATION,
        code=OtpCode("654321"),
        ttl_seconds=180,
        locale="ru",
    )

    assert isinstance(prepared, OtpTelegramMessage)
    assert "654321" in prepared.as_send_text()
    assert "654321" not in repr(prepared)
    assert "654321" not in str(prepared)
    with pytest.raises(ValueError, match="purpose is invalid") as exc_info:
        prepare_otp_telegram_message(
            purpose="REGISTRATION",  # type: ignore[arg-type]
            code=OtpCode("654321"),
            ttl_seconds=180,
            locale="ru",
        )
    assert "654321" not in str(exc_info.value)


def test_registration_message_has_only_code_ttl_and_safety_warning() -> None:
    message = format_registration_otp_message(
        code=OtpCode("112233"),
        ttl_seconds=180,
        locale="uz-Latn",
    ).casefold()

    assert "112233" in message
    assert "3 daqiqa" in message
    assert "so'ramagan" in message
    assert "e'tiborsiz" in message
    for forbidden in (
        "+998",
        "telefon",
        "phone",
        "ism",
        "name",
        "mijoz",
        "customer",
        "shop",
        "taklif",
        "offer",
        "jshshir",
        "passport",
        "document",
        "link",
        "chat",
        "provider",
        "delivered",
        "read",
    ):
        assert forbidden not in message


def test_registration_message_formatter_rejects_invalid_input_without_echo() -> None:
    code = OtpCode("778899")

    for invalid_ttl in (0, True, "180"):
        with pytest.raises(ValueError) as exc_info:
            format_registration_otp_message(
                code=code,
                ttl_seconds=invalid_ttl,  # type: ignore[arg-type]
                locale="uz-Latn",
            )
        assert "778899" not in str(exc_info.value)
