import logging
from dataclasses import is_dataclass

import pytest

from app.otp.code import OTP_CODE_UPPER_BOUND, OtpCode, generate_otp_code


@pytest.mark.parametrize("raw_code", ["000000", "004271", "999999"])
def test_otp_code_accepts_exact_six_ascii_digits(raw_code: str) -> None:
    code = OtpCode(raw_code)

    assert code.as_internal_value() == raw_code


def test_otp_code_user_input_can_trim_outer_whitespace() -> None:
    code = OtpCode.from_user_input(" 004271\n")

    assert code.as_internal_value() == "004271"


@pytest.mark.parametrize(
    "raw_code",
    [
        "",
        "4271",
        "004-271",
        "004 271",
        "１２３４５６",
        "٠٠٤٢٧١",
        "ABC123",
        4271,
    ],
)
def test_otp_code_rejects_non_exact_or_non_ascii_values(raw_code: object) -> None:
    with pytest.raises(ValueError) as exc_info:
        OtpCode(raw_code)  # type: ignore[arg-type]

    if raw_code != "":
        assert str(raw_code) not in str(exc_info.value)
    assert "OTP code" in str(exc_info.value)


@pytest.mark.parametrize(
    ("generated_number", "expected_code"),
    [
        (0, "000000"),
        (4271, "004271"),
        (999999, "999999"),
    ],
)
def test_generate_otp_code_uses_injected_number_generator(
    generated_number: int,
    expected_code: str,
) -> None:
    calls: list[int] = []

    def generator(upper_bound: int) -> int:
        calls.append(upper_bound)
        return generated_number

    code = generate_otp_code(generator)

    assert calls == [OTP_CODE_UPPER_BOUND]
    assert code.as_internal_value() == expected_code


@pytest.mark.parametrize("generated_number", [-1, 1_000_000, True, "123456"])
def test_generate_otp_code_rejects_invalid_generator_output(
    generated_number: object,
) -> None:
    def generator(_: int) -> object:
        return generated_number

    with pytest.raises(ValueError) as exc_info:
        generate_otp_code(generator)  # type: ignore[arg-type]

    assert str(generated_number) not in str(exc_info.value)
    assert "generator" in str(exc_info.value)


def test_generate_otp_code_default_generator_shape() -> None:
    code = generate_otp_code()

    assert len(code.as_internal_value()) == 6
    assert code.as_internal_value().isascii()
    assert code.as_internal_value().isdigit()


def test_otp_code_repr_str_and_logging_are_redacted(caplog) -> None:
    raw_code = "004271"
    code = OtpCode(raw_code)
    logger = logging.getLogger("tests.otp_code")

    with caplog.at_level(logging.INFO):
        logger.info("code %s %r %s", code, code, f"{code}")

    assert raw_code not in repr(code)
    assert raw_code not in str(code)
    assert raw_code not in caplog.text
    assert "redacted" in caplog.text


def test_otp_code_has_no_generic_serialization_api() -> None:
    code = OtpCode("004271")

    assert not is_dataclass(code)
    assert not hasattr(code, "__dict__")
    assert not hasattr(code, "dict")
    assert not hasattr(code, "model_dump")
    assert not hasattr(code, "json")
    assert not hasattr(code, "value")
    assert not hasattr(code, "raw")
    assert not hasattr(code, "secret")
