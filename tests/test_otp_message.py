import inspect

import app.otp.message as message_module
from app.otp.code import OtpCode
from app.otp.message import OTP_MESSAGE_MAX_LENGTH, format_login_otp_message


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
