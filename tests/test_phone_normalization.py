import pytest

from app.auth.phone import (
    CANONICAL_PHONE_LENGTH,
    PhoneNormalizationError,
    normalize_uzbekistan_phone,
)


@pytest.mark.parametrize(
    "raw_phone",
    [
        "+998901234567",
        "998901234567",
        "901234567",
        "+998 90 123 45 67",
        "998-90-123-45-67",
        "(90) 123-45-67",
    ],
)
def test_accepted_uzbekistan_phone_formats_return_same_canonical_value(
    raw_phone: str,
) -> None:
    assert normalize_uzbekistan_phone(raw_phone) == "+998901234567"


@pytest.mark.parametrize(
    "raw_phone",
    [
        "",
        "+997901234567",
        "+99890123456",
        "+9989012345678",
        "90123456",
        "9012345678",
        "+99890abc4567",
        "+998 90 123 45 6a",
        "+998.90.123.45.67",
        "++998901234567",
    ],
)
def test_invalid_uzbekistan_phone_formats_are_rejected(raw_phone: str) -> None:
    with pytest.raises(PhoneNormalizationError) as exc_info:
        normalize_uzbekistan_phone(raw_phone)

    if raw_phone:
        assert raw_phone not in str(exc_info.value)


@pytest.mark.parametrize(
    "raw_phone",
    ["+998901234567", "998901234567", "901234567"],
)
def test_normalized_uzbekistan_phone_is_always_13_character_plus998(
    raw_phone: str,
) -> None:
    canonical_phone = normalize_uzbekistan_phone(raw_phone)

    assert len(canonical_phone) == CANONICAL_PHONE_LENGTH
    assert canonical_phone.startswith("+998")
