from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from typing import Final

OTP_CODE_LENGTH: Final = 6
OTP_CODE_UPPER_BOUND: Final = 10**OTP_CODE_LENGTH
_OTP_CODE_PATTERN: Final = re.compile(r"^[0-9]{6}$", flags=re.ASCII)


class OtpCode:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("OTP code must be a string")
        normalized_value = value.strip()
        if _OTP_CODE_PATTERN.fullmatch(normalized_value) is None:
            raise ValueError("OTP code must be exactly six ASCII digits")
        self._value = normalized_value

    @classmethod
    def from_user_input(cls, value: str) -> OtpCode:
        return cls(value)

    def as_internal_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "OtpCode(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-otp-code>"


def generate_otp_code(
    number_generator: Callable[[int], int] | None = None,
) -> OtpCode:
    resolved_generator = number_generator or secrets.randbelow
    generated_number = resolved_generator(OTP_CODE_UPPER_BOUND)
    if (
        isinstance(generated_number, bool)
        or not isinstance(generated_number, int)
        or generated_number < 0
        or generated_number >= OTP_CODE_UPPER_BOUND
    ):
        raise ValueError("OTP generator returned invalid code")
    return OtpCode(f"{generated_number:06d}")
